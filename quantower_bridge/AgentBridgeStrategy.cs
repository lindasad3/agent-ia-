// Agent IA Forex — bridge Quantower -> agent (order flow temps réel).
//
// À lancer dans Quantower (Strategies Manager) : une instance par instrument (ex. 6E pour EURUSD).
// Envoie toutes les N secondes à l'API de l'agent : delta réel (agresseur), delta cumulé, volumes acheteurs/
// vendeurs, volume profile (POC/VAH/VAL), DOM (taille bid/ask sur N niveaux), déséquilibres diagonaux et
// absorption détectée.
//
// API Quantower utilisée (vérifiée dans github.com/Quantower/Examples) : Strategy (OnRun/OnStop),
// [InputParameter], Core.GetSymbol(symbol.CreateInfo()), Symbol.NewLast (Symbol, Last), Last.Price/Size/AggressorFlag,
// Symbol.DepthOfMarket.GetDepthOfMarketAggregatedCollections(GetDepthOfMarketParameters), Level2Item.Price/Size.

using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Net.Http;
using System.Text;
using System.Threading;
using TradingPlatform.BusinessLayer;

namespace FxAgentBridge
{
    public class AgentBridgeStrategy : Strategy, ICurrentSymbol
    {
        [InputParameter("Symbol (flux Quantower)")]
        public Symbol CurrentSymbol { get; set; }

        [InputParameter("Symbole côté agent (ex. EURUSD)")]
        public string AgentSymbol { get; set; } = "EURUSD";

        [InputParameter("URL de l'agent")]
        public string AgentUrl { get; set; } = "https://votre-vps.exemple.com";

        [InputParameter("Jeton du bridge (FXA_QUANTOWER_TOKEN)")]
        public string BridgeToken { get; set; } = "";

        [InputParameter("Intervalle d'envoi (secondes)", 4, 1, 60, 1, 0)]
        public int IntervalSeconds { get; set; } = 5;

        [InputParameter("Niveaux DOM", 5, 1, 50, 1, 0)]
        public int DomLevels { get; set; } = 10;

        [InputParameter("Ratio de déséquilibre", 6, 1.5, 10, 0.5, 1)]
        public double ImbalanceRatio { get; set; } = 3.0;

        // 6J, 6C, 6S sont cotés en USD par unité de devise : sens inverse de USDJPY, USDCAD, USDCHF
        [InputParameter("Inverser le sens (6J / 6C / 6S)")]
        public bool InvertDirection { get; set; } = false;

        public override string[] MonitoringConnectionsIds => new[] { this.CurrentSymbol?.ConnectionId };

        private readonly object sync = new object();
        private readonly Dictionary<double, double> buyAtPrice = new Dictionary<double, double>();
        private readonly Dictionary<double, double> sellAtPrice = new Dictionary<double, double>();
        private readonly Queue<double> intervalVolumes = new Queue<double>();
        private HttpClient http;
        private Timer timer;
        private double cumDelta, intervalBuy, intervalSell, lastPrice = double.NaN, prevTradePrice = double.NaN;
        private double intervalHigh = double.MinValue, intervalLow = double.MaxValue;
        private int failures;

        public AgentBridgeStrategy() : base()
        {
            this.Name = "Agent IA Forex — Bridge order flow";
            this.Description = "Envoie le delta, le DOM, le volume profile et l'absorption à l'agent IA Forex";
        }

        protected override void OnRun()
        {
            if (this.CurrentSymbol == null || string.IsNullOrWhiteSpace(this.BridgeToken))
            {
                this.LogError("Symbole ou jeton du bridge manquant : arrêtez la stratégie et complétez les paramètres.");
                return;
            }
            this.CurrentSymbol = Core.GetSymbol(this.CurrentSymbol.CreateInfo());
            this.CurrentSymbol.NewLast += this.OnNewLast;
            this.http = new HttpClient { Timeout = TimeSpan.FromSeconds(8) };
            this.http.DefaultRequestHeaders.Add("X-Bridge-Token", this.BridgeToken);
            this.timer = new Timer(_ => this.Push(), null, TimeSpan.FromSeconds(this.IntervalSeconds), TimeSpan.FromSeconds(this.IntervalSeconds));
            this.Log($"Bridge démarré : {this.CurrentSymbol.Name} -> {this.AgentSymbol} ({this.AgentUrl})", StrategyLoggingLevel.Trading);
        }

        protected override void OnStop()
        {
            if (this.CurrentSymbol != null)
                this.CurrentSymbol.NewLast -= this.OnNewLast;
            this.timer?.Dispose();
            this.http?.Dispose();
            this.Log("Bridge arrêté", StrategyLoggingLevel.Trading);
        }

        private void OnNewLast(Symbol symbol, Last last)
        {
            lock (this.sync)
            {
                double size = last.Size, price = last.Price;
                bool isBuy;
                if (last.AggressorFlag == AggressorFlag.Buy) isBuy = true;
                else if (last.AggressorFlag == AggressorFlag.Sell) isBuy = false;
                else isBuy = double.IsNaN(this.prevTradePrice) || price >= this.prevTradePrice; // règle du tick si agresseur inconnu
                this.prevTradePrice = price;
                double p = this.RoundToTick(price);
                var book = isBuy ? this.buyAtPrice : this.sellAtPrice;
                book[p] = (book.TryGetValue(p, out var v) ? v : 0) + size;
                if (isBuy) { this.intervalBuy += size; this.cumDelta += size; }
                else { this.intervalSell += size; this.cumDelta -= size; }
                this.lastPrice = price;
                this.intervalHigh = Math.Max(this.intervalHigh, price);
                this.intervalLow = Math.Min(this.intervalLow, price);
            }
        }

        private double RoundToTick(double price)
        {
            double tick = this.CurrentSymbol?.TickSize ?? 0;
            return tick > 0 ? Math.Round(price / tick) * tick : price;
        }

        private void Push()
        {
            string json;
            try
            {
                json = this.BuildPayload();
            }
            catch (Exception ex)
            {
                this.LogError("Calcul order flow : " + ex.Message);
                return;
            }
            if (json == null)
                return;
            try
            {
                var resp = this.http.PostAsync(this.AgentUrl.TrimEnd('/') + "/api/quantower/ingest",
                    new StringContent(json, Encoding.UTF8, "application/json")).GetAwaiter().GetResult();
                if (!resp.IsSuccessStatusCode)
                    throw new Exception("HTTP " + (int)resp.StatusCode);
                if (this.failures > 0)
                    this.Log("Connexion à l'agent rétablie", StrategyLoggingLevel.Trading);
                this.failures = 0;
            }
            catch (Exception ex)
            {
                this.failures++;
                if (this.failures == 1 || this.failures % 12 == 0)
                    this.LogError($"Envoi à l'agent impossible ({this.failures}) : {ex.Message}");
            }
        }

        private string BuildPayload()
        {
            double buy, sell, cum, last, high, low;
            Dictionary<double, double> b, s;
            lock (this.sync)
            {
                if (double.IsNaN(this.lastPrice))
                    return null;
                buy = this.intervalBuy; sell = this.intervalSell; cum = this.cumDelta; last = this.lastPrice;
                high = this.intervalHigh; low = this.intervalLow;
                b = new Dictionary<double, double>(this.buyAtPrice);
                s = new Dictionary<double, double>(this.sellAtPrice);
                this.intervalBuy = this.intervalSell = 0;
                this.intervalHigh = double.MinValue; this.intervalLow = double.MaxValue;
            }

            // Volume profile de la session de collecte
            var levels = b.Keys.Union(s.Keys).OrderBy(p => p).ToList();
            var total = levels.ToDictionary(p => p, p => (b.TryGetValue(p, out var x) ? x : 0) + (s.TryGetValue(p, out var y) ? y : 0));
            double poc = total.Count > 0 ? total.OrderByDescending(kv => kv.Value).First().Key : last;
            double vah = poc, val = poc;
            double sum = total.Values.Sum(), acc = total.TryGetValue(poc, out var pv) ? pv : 0;
            int iUp = levels.IndexOf(poc), iDn = iUp;
            while (sum > 0 && acc < 0.7 * sum && (iUp < levels.Count - 1 || iDn > 0))
            {
                double up = iUp < levels.Count - 1 ? total[levels[iUp + 1]] : -1;
                double dn = iDn > 0 ? total[levels[iDn - 1]] : -1;
                if (up >= dn) { iUp++; acc += up; vah = levels[iUp]; }
                else { iDn--; acc += dn; val = levels[iDn]; }
            }

            // Déséquilibres diagonaux (achats au prix p vs ventes au prix p - 1 tick)
            var imbalances = new List<string>();
            for (int i = 1; i < levels.Count; i++)
            {
                double bp = b.TryGetValue(levels[i], out var bv) ? bv : 0;
                double sp = s.TryGetValue(levels[i - 1], out var sv) ? sv : 0;
                if (sp > 0 && bp / sp >= this.ImbalanceRatio)
                    imbalances.Add($"{{\"price\":{F(levels[i])},\"side\":\"buy\",\"ratio\":{F(bp / sp)}}}");
                else if (bp > 0 && sp / bp >= this.ImbalanceRatio)
                    imbalances.Add($"{{\"price\":{F(levels[i - 1])},\"side\":\"sell\",\"ratio\":{F(sp / bp)}}}");
            }

            // DOM (taille cumulée des N meilleurs niveaux)
            double domBid = 0, domAsk = 0;
            var dom = this.CurrentSymbol.DepthOfMarket.GetDepthOfMarketAggregatedCollections(new GetDepthOfMarketParameters()
            {
                GetLevel2ItemsParameters = new GetLevel2ItemsParameters()
                {
                    AggregateMethod = AggregateMethod.None,
                    LevelsCount = this.DomLevels,
                }
            });
            if (dom != null)
            {
                domBid = dom.Bids.Take(this.DomLevels).Sum(l => l.Size);
                domAsk = dom.Asks.Take(this.DomLevels).Sum(l => l.Size);
            }

            // Absorption : volume de l'intervalle très supérieur à la moyenne sans progression du prix
            double vol = buy + sell;
            double avg = this.intervalVolumes.Count > 0 ? this.intervalVolumes.Average() : vol;
            this.intervalVolumes.Enqueue(vol);
            while (this.intervalVolumes.Count > 60) this.intervalVolumes.Dequeue();
            double tick = this.CurrentSymbol.TickSize > 0 ? this.CurrentSymbol.TickSize : 0;
            string absorption = "null";
            if (avg > 0 && vol > 2.0 * avg && tick > 0 && high - low <= 2 * tick)
                absorption = (buy - sell) < 0 ? "\"bull\"" : "\"bear\"";

            if (this.InvertDirection)
            {
                // Achat agressif du future = vente de la paire spot ; prix convertis en 1/prix
                double tmp = buy; buy = sell; sell = tmp; cum = -cum;
                tmp = domBid; domBid = domAsk; domAsk = tmp;
                absorption = absorption == "\"bull\"" ? "\"bear\"" : absorption == "\"bear\"" ? "\"bull\"" : absorption;
                double nVah = val > 0 ? 1 / val : 0, nVal = vah > 0 ? 1 / vah : 0;
                poc = poc > 0 ? 1 / poc : 0; vah = nVah; val = nVal; last = last > 0 ? 1 / last : 0;
                imbalances = imbalances.Select(x => x.Replace("\"buy\"", "\"tmp\"").Replace("\"sell\"", "\"buy\"").Replace("\"tmp\"", "\"sell\"")).ToList();
            }

            var sb = new StringBuilder();
            sb.Append("{\"items\":[{");
            sb.Append($"\"symbol\":\"{this.AgentSymbol}\",\"last\":{F(last)},\"delta\":{F(buy - sell)},\"cum_delta\":{F(cum)},");
            sb.Append($"\"buy_volume\":{F(buy)},\"sell_volume\":{F(sell)},\"poc\":{F(poc)},\"vah\":{F(vah)},\"val\":{F(val)},");
            sb.Append($"\"dom_bid\":{F(domBid)},\"dom_ask\":{F(domAsk)},\"absorption\":{absorption},");
            sb.Append("\"imbalances\":[" + string.Join(",", imbalances.Take(20)) + "]");
            sb.Append("}]}");
            return sb.ToString();
        }

        private static string F(double v) => double.IsNaN(v) || double.IsInfinity(v) ? "0" : v.ToString("R", CultureInfo.InvariantCulture);
    }
}

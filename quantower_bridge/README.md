# Bridge Quantower → Agent IA Forex

Stratégie Quantower (C#) qui transmet l'order flow temps réel à l'agent toutes les N secondes :
delta réel (côté agresseur), delta cumulé, volumes acheteurs/vendeurs, volume profile (POC / VAH / VAL),
taille du DOM sur N niveaux, déséquilibres diagonaux et absorption.

## Pourquoi des futures pour le Forex ?

Le spot FX n'a pas de carnet d'ordres centralisé : le volume « tick » d'un broker n'est pas du vrai volume.
Le vrai order flow (delta, DOM, footprint) vient des **futures CME** : 6E (EURUSD), 6B (GBPUSD), 6J (USDJPY,
sens inversé), 6A (AUDUSD), 6C (USDCAD, inversé), 6S (USDCHF, inversé), GC (or). Connectez Quantower à un flux
CME (Rithmic, CQG, dxFeed…) et lancez une instance du bridge par contrat, avec `AgentSymbol` = la paire spot.

## Installation

1. Sur le PC/VPS Quantower : installer le SDK .NET 8.
2. Compiler en pointant vers le dossier `bin` de Quantower :
   `dotnet build -c Release -p:QuantowerBin="C:\Quantower\TradingPlatform\v1.xxx.x\bin"`
3. Copier `bin\Release\FxAgentBridge.dll` dans `C:\Quantower\Settings\Scripts\Strategies\FxAgentBridge\`.
4. Quantower → Strategies Manager → « Agent IA Forex — Bridge order flow » → paramètres :
   Symbol = 6E (flux CME), Symbole côté agent = EURUSD, URL = `https://agent.votre-domaine.com`,
   Jeton = valeur de `FXA_QUANTOWER_TOKEN`, intervalle 5 s → Run.
   Pour 6J, 6C et 6S cochez « Inverser le sens » (delta, DOM, absorption et prix convertis pour la paire spot).
5. Vérifier dans l'application (Settings / pastille Quantower) que le flux est reçu.

L'API Quantower utilisée a été vérifiée dans les exemples officiels (github.com/Quantower/Examples) :
`Strategy.OnRun/OnStop`, `[InputParameter]`, `Core.GetSymbol(symbol.CreateInfo())`, `Symbol.NewLast`,
`Last.Price/Size/AggressorFlag`, `DepthOfMarket.GetDepthOfMarketAggregatedCollections(new GetDepthOfMarketParameters{...})`.
Le plugin n'a pas pu être compilé dans l'environnement de développement (DLL Quantower propriétaire, Windows) :
la première compilation doit être faite sur votre machine Quantower.

## Format envoyé (POST /api/quantower/ingest, en-tête X-Bridge-Token)

```json
{"items":[{"symbol":"EURUSD","last":1.08412,"delta":-152,"cum_delta":3410,"buy_volume":820,"sell_volume":972,
  "poc":1.0838,"vah":1.0846,"val":1.0831,"dom_bid":1450,"dom_ask":980,"absorption":"bull",
  "imbalances":[{"price":1.0841,"side":"buy","ratio":3.4}]}]}
```

Si le flux s'interrompt plus de 30 s, l'agent repasse automatiquement sur les proxys de volume des bougies.

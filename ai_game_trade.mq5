//+------------------------------------------------------------------+
//| ai_game_trade_adaptive_v4.mq5                                   |
//| AI Game Theory Trading System                                   |
//| Production Risk / Broker Adaptation Upgrade                     |
//|                                                                  |
//| IMPORTANT:                                                       |
//| Broker discovery NEVER sends test trades.                       |
//| Filling mode is obtained from the symbol configuration.         |
//| Risk sizing is calculated from the ACTUAL final SL.             |
//+------------------------------------------------------------------+
#property strict
#property version   "4.0"
#property copyright "AI Game Theory Trading System"
#property link      ""
#property description "Nash-style statistical signal + adaptive execution"
#property description "Production broker/risk/execution upgrade"

//+------------------------------------------------------------------+
//| Includes                                                         |
//+------------------------------------------------------------------+
#include <Trade\Trade.mqh>

//+------------------------------------------------------------------+
//| Core Settings                                                    |
//+------------------------------------------------------------------+
input group "=== Core Settings ==="
input string ExpertName          = "AI Game Theory Trader";
input int    MagicNumber         = 2024;
input int    Slippage            = 10;

//+------------------------------------------------------------------+
//| Game Theory Signal                                               |
//+------------------------------------------------------------------+
input group "=== Game Theory Signal ==="
input int     NashPeriod              = 100;
input double  NashDeviationMultiplier = 0.02;
input double  MinConfidenceEntry      = 0.65;
input bool    ValidateNashZone        = true;
input int     MinBarsBetweenTrades   = 3;
input bool    CloseOppositeOnSignal  = true;

//+------------------------------------------------------------------+
//| Risk Management                                                  |
//+------------------------------------------------------------------+
input group "=== Risk Management ==="
input bool    UseAutoLot              = true;
input double  RiskPercent             = 1.0;
input double  FixedLotSize            = 1.0;
input int     MaxOpenTrades           = 5;
input double  MaxDailyLossPercent     = 2.0;
input double  MaxEquityDrawdownPercent = 5.0;

//+------------------------------------------------------------------+
//| Stop Loss                                                        |
//+------------------------------------------------------------------+
input group "=== Stop Loss ==="
input bool    UsePercentSL            = true;     // DEFAULT = FIXED %
input double  StopLossPercent         = 1.5;      // DEFAULT = 1.5%
input double  ATRMultiplierSL         = 1.5;      // Used only if UsePercentSL=false
input double  StopLossPoints          = 200;      // Fallback if ATR disabled/invalid

//+------------------------------------------------------------------+
//| Take Profit                                                      |
//+------------------------------------------------------------------+
input group "=== Take Profit ==="
input bool    UsePercentTP            = false;
input double TakeProfitPercent        = 0.0;
input double ATRMultiplierTP          = 3.0;
input double TakeProfitPoints         = 400;

//+------------------------------------------------------------------+
//| Trailing Stop                                                    |
//+------------------------------------------------------------------+
input group "=== Trailing Stop ==="
input bool    UseTrailingStop         = true;
input bool    TrailByATR              = true;
input double ATRMultiplierTrail       = 1.0;
input double TrailingStopPoints       = 1000;
input double TrailActivatePct         = 5.0;

//+------------------------------------------------------------------+
//| Trade Limits                                                     |
//+------------------------------------------------------------------+
input group "=== Trade Limits ==="
input int     MaxTradesPerDay         = 5;
input int     MaxTradesPerWeek        = 20;
input bool    NoTradeOnFriday         = true;
input bool    EndOfDayClose           = true;
input int     EndOfDayHour            = 23;
input double MaxSpreadPoints          = 30.0;

//+------------------------------------------------------------------+
//| Debug                                                            |
//+------------------------------------------------------------------+
input group "=== Debug ==="
input bool    VerboseLog              = true;
input int     LogEveryNTicks          = 100;

//+------------------------------------------------------------------+
//| Broker Adaptation                                                |
//+------------------------------------------------------------------+
input group "=== Broker Adaptation ==="
input bool    EnableAutoDiscovery     = true;
input bool    ShowDiscoveryDetails    = true;

//+------------------------------------------------------------------+
//| Broker Profile                                                   |
//+------------------------------------------------------------------+
struct SBrokerProfile
{
   int      exec_mode;
   int      filling_flags;

   bool     supports_market;
   bool     supports_instant;
   bool     supports_request;

   double   min_stop_distance_pts;
   double   freeze_distance_pts;

   double   min_lot;
   double   max_lot;
   double   lot_step;

   bool     discovered;
   datetime discovery_time;
};

//+------------------------------------------------------------------+
//| Globals                                                          |
//+------------------------------------------------------------------+
CTrade          trade;

string          g_symbol;
ENUM_TIMEFRAMES g_tf;

datetime        g_lastBarTime  = 0;
datetime        g_lastTradeTime = 0;

int             g_tickCounter  = 0;
int             g_totalTrades  = 0;

int             g_todayTrades  = 0;
int             g_weekTrades   = 0;

datetime        g_lastResetDay  = 0;
datetime        g_lastResetWeek = 0;

double          g_dayStartEquity = 0.0;
double          g_peakEquity     = 0.0;

int             g_hRSI = INVALID_HANDLE;
int             g_hATR = INVALID_HANDLE;

SBrokerProfile  g_brokerProfile;
bool            g_discoveryComplete = false;

//+------------------------------------------------------------------+
//| Day name                                                         |
//+------------------------------------------------------------------+
string DayName(const int d)
{
   string n[] =
   {
      "Sunday",
      "Monday",
      "Tuesday",
      "Wednesday",
      "Thursday",
      "Friday",
      "Saturday"
   };

   if(d >= 0 && d <= 6)
      return n[d];

   return "Unknown";
}

//+------------------------------------------------------------------+
//| Current day of week                                              |
//+------------------------------------------------------------------+
int CurrentDayOfWeek()
{
   MqlDateTime dt;
   TimeToStruct(TimeCurrent(), dt);
   return dt.day_of_week;
}

//+------------------------------------------------------------------+
//| Start of current day                                             |
//+------------------------------------------------------------------+
datetime CurrentDayStart()
{
   MqlDateTime dt;
   TimeToStruct(TimeCurrent(), dt);

   dt.hour = 0;
   dt.min  = 0;
   dt.sec  = 0;

   return StructToTime(dt);
}

//+------------------------------------------------------------------+
//| Start of current week                                            |
//+------------------------------------------------------------------+
datetime CurrentWeekStart()
{
   MqlDateTime dt;
   TimeToStruct(TimeCurrent(), dt);

   int daysFromMonday = (dt.day_of_week + 6) % 7;

   dt.day -= daysFromMonday;
   dt.hour = 0;
   dt.min  = 0;
   dt.sec  = 0;

   return StructToTime(dt);
}

//+------------------------------------------------------------------+
//| Volume digits                                                     |
//+------------------------------------------------------------------+
int VolumeDigits()
{
   double step = SymbolInfoDouble(g_symbol, SYMBOL_VOLUME_STEP);

   if(step >= 1.0)
      return 0;

   if(step >= 0.1)
      return 1;

   if(step >= 0.01)
      return 2;

   if(step >= 0.001)
      return 3;

   return 4;
}

//+------------------------------------------------------------------+
//| Normalize volume                                                 |
//+------------------------------------------------------------------+
double NormalizeVolumeDown(const double volume)
{
   double step   = SymbolInfoDouble(g_symbol, SYMBOL_VOLUME_STEP);
   double minLot = SymbolInfoDouble(g_symbol, SYMBOL_VOLUME_MIN);
   double maxLot = SymbolInfoDouble(g_symbol, SYMBOL_VOLUME_MAX);

   if(step <= 0 || minLot <= 0 || maxLot <= 0)
      return 0.0;

   double normalized = MathFloor(volume / step) * step;

   normalized = NormalizeDouble(normalized, VolumeDigits());

   if(normalized > maxLot)
      normalized = maxLot;

   if(normalized < minLot)
      return 0.0;

   return normalized;
}

//+------------------------------------------------------------------+
//| Load broker profile                                              |
//| No test orders are ever sent.                                    |
//+------------------------------------------------------------------+
bool LoadBrokerProfile()
{
   ZeroMemory(g_brokerProfile);

   g_brokerProfile.exec_mode =
      (int)SymbolInfoInteger(g_symbol, SYMBOL_TRADE_EXEMODE);

   g_brokerProfile.filling_flags =
      (int)SymbolInfoInteger(g_symbol, SYMBOL_FILLING_MODE);

   g_brokerProfile.supports_market =
      (g_brokerProfile.exec_mode == SYMBOL_TRADE_EXECUTION_MARKET);

   g_brokerProfile.supports_instant =
      (g_brokerProfile.exec_mode == SYMBOL_TRADE_EXECUTION_INSTANT);

   g_brokerProfile.supports_request =
      (g_brokerProfile.exec_mode == SYMBOL_TRADE_EXECUTION_REQUEST);

   g_brokerProfile.min_stop_distance_pts =
      (double)SymbolInfoInteger(g_symbol, SYMBOL_TRADE_STOPS_LEVEL);

   g_brokerProfile.freeze_distance_pts =
      (double)SymbolInfoInteger(g_symbol, SYMBOL_TRADE_FREEZE_LEVEL);

   g_brokerProfile.min_lot =
      SymbolInfoDouble(g_symbol, SYMBOL_VOLUME_MIN);

   g_brokerProfile.max_lot =
      SymbolInfoDouble(g_symbol, SYMBOL_VOLUME_MAX);

   g_brokerProfile.lot_step =
      SymbolInfoDouble(g_symbol, SYMBOL_VOLUME_STEP);

   if(g_brokerProfile.min_lot <= 0)
      return false;

   if(g_brokerProfile.max_lot <= 0)
      return false;

   if(g_brokerProfile.lot_step <= 0)
      return false;

   g_brokerProfile.discovered     = true;
   g_brokerProfile.discovery_time = TimeCurrent();

   g_discoveryComplete = true;

   trade.SetTypeFillingBySymbol(g_symbol);

   if(ShowDiscoveryDetails)
   {
      Print("============================================================");
      Print("[BROKER] Symbol capability discovery completed");
      PrintFormat("[BROKER] Execution mode: %d",
                  g_brokerProfile.exec_mode);
      PrintFormat("[BROKER] Filling flags: %d",
                  g_brokerProfile.filling_flags);
      PrintFormat("[BROKER] Min lot: %.8f",
                  g_brokerProfile.min_lot);
      PrintFormat("[BROKER] Max lot: %.8f",
                  g_brokerProfile.max_lot);
      PrintFormat("[BROKER] Lot step: %.8f",
                  g_brokerProfile.lot_step);
      PrintFormat("[BROKER] Stop level: %.0f points",
                  g_brokerProfile.min_stop_distance_pts);
      PrintFormat("[BROKER] Freeze level: %.0f points",
                  g_brokerProfile.freeze_distance_pts);
      Print("[BROKER] No discovery/test orders were submitted");
      Print("============================================================");
   }

   return true;
}

//+------------------------------------------------------------------+
//| Required minimum stop distance                                   |
//+------------------------------------------------------------------+
double RequiredStopDistancePrice()
{
   double point = SymbolInfoDouble(g_symbol, SYMBOL_POINT);

   if(point <= 0)
      return 0.0;

   double requiredPts =
      MathMax(g_brokerProfile.min_stop_distance_pts,
              g_brokerProfile.freeze_distance_pts);

   return requiredPts * point;
}

//+------------------------------------------------------------------+
//| Validate and adjust SL/TP                                        |
//+------------------------------------------------------------------+
void ValidateStopLevels(const int direction,
                        const double entryPrice,
                        double &sl,
                        double &tp)
{
   double requiredDistance = RequiredStopDistancePrice();

   if(requiredDistance <= 0)
      return;

   int digits =
      (int)SymbolInfoInteger(g_symbol, SYMBOL_DIGITS);

   if(sl != 0.0)
   {
      double distance =
         direction == ORDER_TYPE_BUY
         ? entryPrice - sl
         : sl - entryPrice;

      if(distance > 0.0 && distance < requiredDistance)
      {
         if(direction == ORDER_TYPE_BUY)
            sl = entryPrice - requiredDistance;
         else
            sl = entryPrice + requiredDistance;
      }
   }

   if(tp != 0.0)
   {
      double distance =
         direction == ORDER_TYPE_BUY
         ? tp - entryPrice
         : entryPrice - tp;

      if(distance > 0.0 && distance < requiredDistance)
      {
         if(direction == ORDER_TYPE_BUY)
            tp = entryPrice + requiredDistance;
         else
            tp = entryPrice - requiredDistance;
      }
   }

   sl = NormalizeDouble(sl, digits);
   tp = NormalizeDouble(tp, digits);
}

//+------------------------------------------------------------------+
//| Calculate SL/TP                                                  |
//| Default SL = fixed percentage.                                   |
//+------------------------------------------------------------------+
void CalcSLTP(const int direction,
              const double entryPrice,
              const double atrVal,
              double &sl,
              double &tp)
{
   int digits =
      (int)SymbolInfoInteger(g_symbol, SYMBOL_DIGITS);

   double point =
      SymbolInfoDouble(g_symbol, SYMBOL_POINT);

   double slDist = 0.0;
   double tpDist = 0.0;

   // ---------------------------------------------------------------
   // DEFAULT: fixed percentage SL
   // ---------------------------------------------------------------
   if(UsePercentSL)
   {
      slDist = entryPrice * StopLossPercent / 100.0;
   }
   else if(atrVal > 0.0)
   {
      slDist = atrVal * ATRMultiplierSL;
   }
   else
   {
      slDist = StopLossPoints * point;
   }

   // ---------------------------------------------------------------
   // TP
   // ---------------------------------------------------------------
   if(UsePercentTP && TakeProfitPercent > 0.0)
   {
      tpDist = entryPrice * TakeProfitPercent / 100.0;
   }
   else if(atrVal > 0.0)
   {
      tpDist = atrVal * ATRMultiplierTP;
   }
   else
   {
      tpDist = TakeProfitPoints * point;
   }

   if(slDist <= 0.0)
      slDist = StopLossPoints * point;

   if(tpDist <= 0.0)
      tpDist = TakeProfitPoints * point;

   if(direction == ORDER_TYPE_BUY)
   {
      sl = entryPrice - slDist;
      tp = entryPrice + tpDist;
   }
   else
   {
      sl = entryPrice + slDist;
      tp = entryPrice - tpDist;
   }

   sl = NormalizeDouble(sl, digits);
   tp = NormalizeDouble(tp, digits);

   ValidateStopLevels(direction, entryPrice, sl, tp);
}

//+------------------------------------------------------------------+
//| Calculate risk-based lot size using actual SL                    |
//+------------------------------------------------------------------+
double CalcRiskLotSize(const int direction,
                       const double entryPrice,
                       const double stopPrice)
{
   if(!UseAutoLot)
      return NormalizeVolumeDown(FixedLotSize);

   if(entryPrice <= 0.0 || stopPrice <= 0.0)
      return 0.0;

   double balance =
      AccountInfoDouble(ACCOUNT_BALANCE);

   double riskMoney =
      balance * RiskPercent / 100.0;

   if(riskMoney <= 0.0)
      return 0.0;

   ENUM_ORDER_TYPE orderType =
      direction == ORDER_TYPE_BUY
      ? ORDER_TYPE_BUY
      : ORDER_TYPE_SELL;

   double lossPerLot = 0.0;

   if(!OrderCalcProfit(orderType,
                       g_symbol,
                       1.0,
                       entryPrice,
                       stopPrice,
                       lossPerLot))
   {
      if(VerboseLog)
         PrintFormat("[RISK] OrderCalcProfit failed. Error=%d",
                     GetLastError());

      return 0.0;
   }

   lossPerLot = MathAbs(lossPerLot);

   if(lossPerLot <= 0.0)
      return 0.0;

   double rawLots =
      riskMoney / lossPerLot;

   // Risk must never be rounded upward.
   double lots =
      NormalizeVolumeDown(rawLots);

   if(lots <= 0.0)
   {
      if(VerboseLog)
      {
         PrintFormat("[RISK] Required volume %.8f is below broker minimum %.8f",
                     rawLots,
                     SymbolInfoDouble(g_symbol, SYMBOL_VOLUME_MIN));
      }

      return 0.0;
   }

   return lots;
}

//+------------------------------------------------------------------+
//| Mean and standard deviation                                     |
//+------------------------------------------------------------------+
void CalcMeanStdDev(double &data[],
                    const int period,
                    double &mean,
                    double &stddev)
{
   mean   = 0.0;
   stddev = 0.0;

   if(period <= 0)
      return;

   int available = ArraySize(data);

   if(available < period)
      return;

   for(int i = 0; i < period; i++)
      mean += data[i];

   mean /= period;

   for(int i = 0; i < period; i++)
   {
      double delta = data[i] - mean;
      stddev += delta * delta;
   }

   stddev = MathSqrt(stddev / period);
}

//+------------------------------------------------------------------+
//| MFG-style statistical score                                      |
//+------------------------------------------------------------------+
double CalcMFGScore(double &prices[],
                    long &volumes[],
                    const double atr)
{
   int sz = ArraySize(prices) - 1;

   if(sz <= 1 || atr <= 0.0)
      return 0.5;

   double returns[];
   ArrayResize(returns, sz);

   for(int i = 0; i < sz; i++)
   {
      if(prices[i + 1] != 0.0)
         returns[i] =
            (prices[i] - prices[i + 1]) / prices[i + 1];
      else
         returns[i] = 0.0;
   }

   double retMean  = 0.0;
   double volatility = 0.0;

   CalcMeanStdDev(returns,
                  sz,
                  retMean,
                  volatility);

   double volMom = 1.0;

   if(volumes[1] > 0)
      volMom =
         (double)volumes[0] /
         (double)volumes[1];

   double normalizedVolatility =
      volatility /
      (atr / MathMax(prices[0], 0.00000001));

   normalizedVolatility =
      MathMax(0.0,
              MathMin(2.0,
                      normalizedVolatility));

   double returnMagnitude =
      MathAbs(returns[0]) /
      (atr / MathMax(prices[0], 0.00000001));

   returnMagnitude =
      MathMax(0.0,
              MathMin(2.0,
                      returnMagnitude));

   double volumeComponent =
      MathMin(volMom, 2.0) / 2.0;

   double volatilityComponent =
      1.0 -
      normalizedVolatility / 2.0;

   double returnComponent =
      1.0 -
      returnMagnitude / 2.0;

   double score = 0.0;

   score += 0.4 * volatilityComponent;
   score += 0.3 * volumeComponent;
   score += 0.3 * returnComponent;

   return MathMax(0.0,
                  MathMin(1.0,
                          score));
}

//+------------------------------------------------------------------+
//| TP feasibility score                                             |
//+------------------------------------------------------------------+
double SimTPProbability(double &prices[],
                        const double rsi,
                        const double atr)
{
   int limit =
      MathMin(10,
              ArraySize(prices) - 1);

   if(limit <= 0 || atr <= 0.0)
      return 0.5;

   double avgMove = 0.0;

   for(int i = 0; i < limit; i++)
   {
      if(prices[i + 1] != 0.0)
      {
         avgMove +=
            MathAbs(prices[i] - prices[i + 1]) /
            prices[i + 1];
      }
   }

   avgMove /= limit;

   double normalizedMove =
      avgMove /
      (atr / MathMax(prices[0], 0.00000001));

   normalizedMove =
      MathMax(0.0,
              MathMin(2.0,
                      normalizedMove));

   double rsiComponent =
      1.0 -
      MathAbs(rsi - 50.0) / 50.0;

   rsiComponent =
      MathMax(0.0,
              MathMin(1.0,
                      rsiComponent));

   double trendComponent = 0.5;

   if(ArraySize(prices) > 5)
   {
      if(prices[0] > prices[5])
         trendComponent = 0.6;
      else if(prices[0] < prices[5])
         trendComponent = 0.4;
   }

   double prob = 0.5;

   prob += 0.2 * (1.0 - normalizedMove / 2.0);
   prob += 0.2 * rsiComponent;
   prob += 0.1 * trendComponent;

   return MathMax(0.0,
                  MathMin(1.0,
                          prob));
}

//+------------------------------------------------------------------+
//| Exit score                                                       |
//+------------------------------------------------------------------+
double SimExitProbability(const double profitPct,
                          const double atr,
                          const double price)
{
   if(price <= 0.0 || atr <= 0.0)
      return 0.5;

   double atrPct =
      atr / price;

   if(atrPct <= 0.0)
      return 0.5;

   double target =
      ATRMultiplierTP *
      atrPct;

   if(target <= 0.0)
      return 0.5;

   if(profitPct >= target)
      return 1.0;

   double progress =
      profitPct / target;

   progress =
      MathMax(0.0,
              MathMin(1.0,
                      progress));

   return progress;
}

//+------------------------------------------------------------------+
//| Count EA positions                                               |
//+------------------------------------------------------------------+
int CountPositions(const int direction = 0)
{
   int count = 0;

   for(int i = 0; i < PositionsTotal(); i++)
   {
      ulong ticket =
         PositionGetTicket(i);

      if(ticket == 0)
         continue;

      if(!PositionSelectByTicket(ticket))
         continue;

      if(PositionGetString(POSITION_SYMBOL) != g_symbol)
         continue;

      if(PositionGetInteger(POSITION_MAGIC) != MagicNumber)
         continue;

      long type =
         PositionGetInteger(POSITION_TYPE);

      if(direction == 0)
      {
         count++;
      }
      else if(direction == 1 &&
              type == POSITION_TYPE_BUY)
      {
         count++;
      }
      else if(direction == -1 &&
              type == POSITION_TYPE_SELL)
      {
         count++;
      }
   }

   return count;
}

//+------------------------------------------------------------------+
//| Close positions                                                  |
//+------------------------------------------------------------------+
void ClosePositions(const int direction)
{
   for(int i = PositionsTotal() - 1;
       i >= 0;
       i--)
   {
      ulong ticket =
         PositionGetTicket(i);

      if(ticket == 0)
         continue;

      if(!PositionSelectByTicket(ticket))
         continue;

      if(PositionGetString(POSITION_SYMBOL) != g_symbol)
         continue;

      if(PositionGetInteger(POSITION_MAGIC) != MagicNumber)
         continue;

      long type =
         PositionGetInteger(POSITION_TYPE);

      bool isBuy =
         (type == POSITION_TYPE_BUY);

      bool isSell =
         (type == POSITION_TYPE_SELL);

      if((direction == 1 && isBuy) ||
         (direction == -1 && isSell))
      {
         bool requestOK =
            trade.PositionClose(ticket);

         uint retcode =
            trade.ResultRetcode();

         if(requestOK &&
            (retcode == TRADE_RETCODE_DONE ||
             retcode == TRADE_RETCODE_DONE_PARTIAL))
         {
            if(VerboseLog)
            {
               PrintFormat("[CLOSE] %s #%I64u closed",
                           isBuy ? "BUY" : "SELL",
                           ticket);
            }
         }
         else
         {
            PrintFormat("[CLOSE] FAILED #%I64u rc=%u %s",
                        ticket,
                        retcode,
                        trade.ResultRetcodeDescription());
         }
      }
   }
}

//+------------------------------------------------------------------+
//| Current spread check                                             |
//+------------------------------------------------------------------+
bool SpreadAllowed()
{
   MqlTick tick;

   if(!SymbolInfoTick(g_symbol, tick))
      return false;

   double point =
      SymbolInfoDouble(g_symbol, SYMBOL_POINT);

   if(point <= 0.0)
      return false;

   double spreadPts =
      (tick.ask - tick.bid) / point;

   if(MaxSpreadPoints > 0.0 &&
      spreadPts > MaxSpreadPoints)
   {
      if(VerboseLog)
      {
         PrintFormat("[FILTER] Spread %.1f > maximum %.1f points",
                     spreadPts,
                     MaxSpreadPoints);
      }

      return false;
   }

   return true;
}

//+------------------------------------------------------------------+
//| Symbol trading mode                                              |
//+------------------------------------------------------------------+
bool SymbolTradeModeAllows(const int direction)
{
   ENUM_SYMBOL_TRADE_MODE mode =
      (ENUM_SYMBOL_TRADE_MODE)
      SymbolInfoInteger(g_symbol,
                        SYMBOL_TRADE_MODE);

   if(mode == SYMBOL_TRADE_MODE_DISABLED)
      return false;

   if(mode == SYMBOL_TRADE_MODE_CLOSEONLY)
      return false;

   if(direction == ORDER_TYPE_BUY &&
      mode == SYMBOL_TRADE_MODE_SHORTONLY)
      return false;

   if(direction == ORDER_TYPE_SELL &&
      mode == SYMBOL_TRADE_MODE_LONGONLY)
      return false;

   return true;
}

//+------------------------------------------------------------------+
//| Daily / weekly reset                                             |
//+------------------------------------------------------------------+
void ResetDailyCounter()
{
   datetime today =
      CurrentDayStart();

   if(g_lastResetDay == 0)
   {
      g_lastResetDay = today;
      return;
   }

   if(today != g_lastResetDay)
   {
      if(VerboseLog)
      {
         PrintFormat("[LIMIT] New day. Daily trades reset %d -> 0",
                     g_todayTrades);
      }

      g_todayTrades =
         0;

      g_lastResetDay =
         today;

      g_dayStartEquity =
         AccountInfoDouble(ACCOUNT_EQUITY);
   }
}

//+------------------------------------------------------------------+
//| Weekly reset                                                     |
//+------------------------------------------------------------------+
void ResetWeeklyCounter()
{
   datetime week =
      CurrentWeekStart();

   if(g_lastResetWeek == 0)
   {
      g_lastResetWeek = week;
      return;
   }

   if(week != g_lastResetWeek)
   {
      if(VerboseLog)
      {
         PrintFormat("[LIMIT] New week. Weekly trades reset %d -> 0",
                     g_weekTrades);
      }

      g_weekTrades =
         0;

      g_lastResetWeek =
         week;
   }
}

//+------------------------------------------------------------------+
//| Rebuild counters from history                                    |
//+------------------------------------------------------------------+
void RebuildTradeCounters()
{
   g_todayTrades = 0;
   g_weekTrades  = 0;

   datetime now =
      TimeCurrent();

   datetime dayStart =
      CurrentDayStart();

   datetime weekStart =
      CurrentWeekStart();

   if(!HistorySelect(weekStart, now))
      return;

   int total =
      HistoryDealsTotal();

   for(int i = 0; i < total; i++)
   {
      ulong ticket =
         HistoryDealGetTicket(i);

      if(ticket == 0)
         continue;

      string symbol =
         HistoryDealGetString(ticket,
                              DEAL_SYMBOL);

      if(symbol != g_symbol)
         continue;

      long magic =
         HistoryDealGetInteger(ticket,
                               DEAL_MAGIC);

      if(magic != MagicNumber)
         continue;

      long entry =
         HistoryDealGetInteger(ticket,
                               DEAL_ENTRY);

      if(entry != DEAL_ENTRY_IN)
         continue;

      datetime dealTime =
         (datetime)
         HistoryDealGetInteger(ticket,
                               DEAL_TIME);

      g_weekTrades++;

      if(dealTime >= dayStart)
         g_todayTrades++;
   }
}

//+------------------------------------------------------------------+
//| Record trade                                                     |
//+------------------------------------------------------------------+
void RecordTrade()
{
   g_totalTrades++;
   g_todayTrades++;
   g_weekTrades++;

   if(VerboseLog)
   {
      PrintFormat("[LIMIT] Trade recorded Total=%d Today=%d/%d Week=%d/%d",
                  g_totalTrades,
                  g_todayTrades,
                  MaxTradesPerDay,
                  g_weekTrades,
                  MaxTradesPerWeek);
   }
}

//+------------------------------------------------------------------+
//| Daily/equity risk gate                                           |
//+------------------------------------------------------------------+
bool EquityRiskAllowed()
{
   double equity =
      AccountInfoDouble(ACCOUNT_EQUITY);

   if(equity <= 0.0)
      return false;

   if(g_dayStartEquity <= 0.0)
      g_dayStartEquity = equity;

   if(g_peakEquity <= 0.0)
      g_peakEquity = equity;

   if(equity > g_peakEquity)
      g_peakEquity = equity;

   if(MaxDailyLossPercent > 0.0)
   {
      double dailyLossPct =
         (g_dayStartEquity - equity) /
         g_dayStartEquity *
         100.0;

      if(dailyLossPct >= MaxDailyLossPercent)
      {
         if(VerboseLog)
         {
            PrintFormat("[RISK] Daily loss %.2f%% >= %.2f%%",
                        dailyLossPct,
                        MaxDailyLossPercent);
         }

         return false;
      }
   }

   if(MaxEquityDrawdownPercent > 0.0)
   {
      double drawdownPct =
         (g_peakEquity - equity) /
         g_peakEquity *
         100.0;

      if(drawdownPct >= MaxEquityDrawdownPercent)
      {
         if(VerboseLog)
         {
            PrintFormat("[RISK] Equity drawdown %.2f%% >= %.2f%%",
                        drawdownPct,
                        MaxEquityDrawdownPercent);
         }

         return false;
      }
   }

   return true;
}

//+------------------------------------------------------------------+
//| General entry permission                                         |
//+------------------------------------------------------------------+
bool TradingAllowed(const int direction)
{
   ResetDailyCounter();
   ResetWeeklyCounter();

   if(!EquityRiskAllowed())
      return false;

   if(!SpreadAllowed())
      return false;

   if(!SymbolTradeModeAllows(direction))
      return false;

   if(NoTradeOnFriday &&
      CurrentDayOfWeek() == 5)
   {
      if(VerboseLog)
         Print("[LIMIT] Friday - no new trades");

      return false;
   }

   if(MaxTradesPerDay > 0 &&
      g_todayTrades >= MaxTradesPerDay)
   {
      if(VerboseLog)
      {
         PrintFormat("[LIMIT] Daily limit %d/%d",
                     g_todayTrades,
                     MaxTradesPerDay);
      }

      return false;
   }

   if(MaxTradesPerWeek > 0 &&
      g_weekTrades >= MaxTradesPerWeek)
   {
      if(VerboseLog)
      {
         PrintFormat("[LIMIT] Weekly limit %d/%d",
                     g_weekTrades,
                     MaxTradesPerWeek);
      }

      return false;
   }

   if(MaxOpenTrades > 0 &&
      CountPositions(0) >= MaxOpenTrades)
   {
      if(VerboseLog)
      {
         PrintFormat("[LIMIT] Open positions %d/%d",
                     CountPositions(0),
                     MaxOpenTrades);
      }

      return false;
   }

   if(MinBarsBetweenTrades > 0 &&
      g_lastTradeTime > 0)
   {
      int shift =
         iBarShift(g_symbol,
                   g_tf,
                   g_lastTradeTime,
                   false);

      if(shift >= 0 &&
         shift < MinBarsBetweenTrades)
      {
         if(VerboseLog)
         {
            PrintFormat("[FILTER] %d/%d bars since last trade",
                        shift,
                        MinBarsBetweenTrades);
         }

         return false;
      }
   }

   return true;
}

//+------------------------------------------------------------------+
//| Order validation                                                 |
//+------------------------------------------------------------------+
bool ValidateOrderRequest(const int direction,
                          const double lots,
                          const double price,
                          const double sl,
                          const double tp)
{
   MqlTradeRequest request;
   MqlTradeCheckResult check;

   ZeroMemory(request);
   ZeroMemory(check);

   request.action =
      TRADE_ACTION_DEAL;

   request.symbol =
      g_symbol;

   request.magic =
      MagicNumber;

   request.volume =
      lots;

   request.type =
      direction == ORDER_TYPE_BUY
      ? ORDER_TYPE_BUY
      : ORDER_TYPE_SELL;

   request.price =
      price;

   request.sl =
      sl;

   request.tp =
      tp;

   request.deviation =
      Slippage;

   request.type_time =
      ORDER_TIME_GTC;

   request.type_filling =
      ORDER_FILLING_FOK;

   if(!OrderCheck(request, check))
   {
      PrintFormat("[CHECK] OrderCheck failed. Error=%d",
                  GetLastError());

      return false;
   }

   if(check.retcode != TRADE_RETCODE_DONE)
   {
      PrintFormat("[CHECK] REJECTED rc=%u comment=%s",
                  check.retcode,
                  check.comment);

      return false;
   }

   return true;
}

//+------------------------------------------------------------------+
//| Open position                                                    |
//+------------------------------------------------------------------+
bool OpenPosition(const int direction,
                  const double lots,
                  double sl,
                  double tp,
                  const double confidence)
{
   if(lots <= 0.0)
      return false;

   MqlTick tick;

   if(!SymbolInfoTick(g_symbol, tick))
      return false;

   double price =
      direction == ORDER_TYPE_BUY
      ? tick.ask
      : tick.bid;

   ValidateStopLevels(direction,
                      price,
                      sl,
                      tp);

   string comment =
      StringFormat("GameTrade_%.2f",
                   confidence);

   // Select broker-supported filling policy.
   trade.SetTypeFillingBySymbol(g_symbol);
   trade.SetExpertMagicNumber(MagicNumber);
   trade.SetDeviationInPoints(Slippage);

   if(!ValidateOrderRequest(direction,
                            lots,
                            price,
                            sl,
                            tp))
   {
      return false;
   }

   bool requestOK = false;

   if(direction == ORDER_TYPE_BUY)
   {
      requestOK =
         trade.Buy(lots,
                   g_symbol,
                   0.0,
                   sl,
                   tp,
                   comment);
   }
   else
   {
      requestOK =
         trade.Sell(lots,
                    g_symbol,
                    0.0,
                    sl,
                    tp,
                    comment);
   }

   uint retcode =
      trade.ResultRetcode();

   if(!requestOK)
   {
      PrintFormat("[OPEN] Request failed %s rc=%u %s",
                  direction == ORDER_TYPE_BUY
                  ? "BUY"
                  : "SELL",
                  retcode,
                  trade.ResultRetcodeDescription());

      return false;
   }

   if(retcode != TRADE_RETCODE_DONE &&
      retcode != TRADE_RETCODE_DONE_PARTIAL &&
      retcode != TRADE_RETCODE_PLACED)
   {
      PrintFormat("[OPEN] Server rejected %s rc=%u %s",
                  direction == ORDER_TYPE_BUY
                  ? "BUY"
                  : "SELL",
                  retcode,
                  trade.ResultRetcodeDescription());

      return false;
   }

   PrintFormat("[OPEN] %s lots=%.4f entry=%.5f SL=%.5f TP=%.5f order=%I64u deal=%I64u",
               direction == ORDER_TYPE_BUY
               ? "BUY"
               : "SELL",
               lots,
               price,
               sl,
               tp,
               trade.ResultOrder(),
               trade.ResultDeal());

   return true;
}

//+------------------------------------------------------------------+
//| Trailing stop                                                    |
//+------------------------------------------------------------------+
void ManageTrailingStop(const double atrVal)
{
   if(!UseTrailingStop)
      return;

   double point =
      SymbolInfoDouble(g_symbol,
                       SYMBOL_POINT);

   if(point <= 0.0)
      return;

   double trailDist =
      TrailByATR
      ? atrVal * ATRMultiplierTrail
      : TrailingStopPoints * point;

   if(trailDist <= 0.0)
      return;

   double requiredDistance =
      RequiredStopDistancePrice();

   if(trailDist < requiredDistance)
      trailDist = requiredDistance;

   for(int i = PositionsTotal() - 1;
       i >= 0;
       i--)
   {
      ulong ticket =
         PositionGetTicket(i);

      if(ticket == 0)
         continue;

      if(!PositionSelectByTicket(ticket))
         continue;

      if(PositionGetString(POSITION_SYMBOL) != g_symbol)
         continue;

      if(PositionGetInteger(POSITION_MAGIC) != MagicNumber)
         continue;

      long type =
         PositionGetInteger(POSITION_TYPE);

      double openPrice =
         PositionGetDouble(POSITION_PRICE_OPEN);

      double currentSL =
         PositionGetDouble(POSITION_SL);

      double currentTP =
         PositionGetDouble(POSITION_TP);

      MqlTick tick;

      if(!SymbolInfoTick(g_symbol, tick))
         continue;

      int digits =
         (int)SymbolInfoInteger(g_symbol,
                                SYMBOL_DIGITS);

      if(type == POSITION_TYPE_BUY)
      {
         double profitPct =
            openPrice > 0.0
            ? (tick.bid - openPrice) /
              openPrice *
              100.0
            : 0.0;

         if(profitPct < TrailActivatePct)
            continue;

         double newSL =
            tick.bid - trailDist;

         newSL =
            NormalizeDouble(newSL,
                            digits);

         if(newSL <= 0.0)
            continue;

         if(currentSL != 0.0 &&
            newSL <= currentSL + point)
            continue;

         trade.SetTypeFillingBySymbol(g_symbol);

         bool ok =
            trade.PositionModify(ticket,
                                 newSL,
                                 currentTP);

         uint rc =
            trade.ResultRetcode();

         if(!ok ||
            (rc != TRADE_RETCODE_DONE &&
             rc != TRADE_RETCODE_NO_CHANGES))
         {
            if(VerboseLog)
            {
               PrintFormat("[TRAIL] BUY #%I64u failed rc=%u %s",
                           ticket,
                           rc,
                           trade.ResultRetcodeDescription());
            }
         }
      }
      else if(type == POSITION_TYPE_SELL)
      {
         double profitPct =
            openPrice > 0.0
            ? (openPrice - tick.ask) /
              openPrice *
              100.0
            : 0.0;

         if(profitPct < TrailActivatePct)
            continue;

         double newSL =
            tick.ask + trailDist;

         newSL =
            NormalizeDouble(newSL,
                            digits);

         if(currentSL != 0.0 &&
            newSL >= currentSL - point)
            continue;

         trade.SetTypeFillingBySymbol(g_symbol);

         bool ok =
            trade.PositionModify(ticket,
                                 newSL,
                                 currentTP);

         uint rc =
            trade.ResultRetcode();

         if(!ok ||
            (rc != TRADE_RETCODE_DONE &&
             rc != TRADE_RETCODE_NO_CHANGES))
         {
            if(VerboseLog)
            {
               PrintFormat("[TRAIL] SELL #%I64u failed rc=%u %s",
                           ticket,
                           rc,
                           trade.ResultRetcodeDescription());
            }
         }
      }
   }
}

//+------------------------------------------------------------------+
//| Manage open positions                                            |
//+------------------------------------------------------------------+
void ManageOpenPositions(const double atrVal)
{
   MqlDateTime dt;
   TimeToStruct(TimeCurrent(), dt);

   bool isEOD =
      EndOfDayClose &&
      dt.hour >= EndOfDayHour;

   for(int i = PositionsTotal() - 1;
       i >= 0;
       i--)
   {
      ulong ticket =
         PositionGetTicket(i);

      if(ticket == 0)
         continue;

      if(!PositionSelectByTicket(ticket))
         continue;

      if(PositionGetString(POSITION_SYMBOL) != g_symbol)
         continue;

      if(PositionGetInteger(POSITION_MAGIC) != MagicNumber)
         continue;

      long type =
         PositionGetInteger(POSITION_TYPE);

      double openPrice =
         PositionGetDouble(POSITION_PRICE_OPEN);

      double currentPrice =
         PositionGetDouble(POSITION_PRICE_CURRENT);

      double profitPct =
         openPrice > 0.0
         ? ((type == POSITION_TYPE_BUY)
            ? (currentPrice - openPrice) / openPrice
            : (openPrice - currentPrice) / openPrice)
         : 0.0;

      bool exitNow = false;
      string exitReason = "";

      double exitScore =
         SimExitProbability(profitPct,
                            atrVal,
                            openPrice);

      if(exitScore < 0.30 &&
         profitPct > 0.0)
      {
         exitNow = true;
         exitReason = "MFG_OPTIMAL";
      }

      if(isEOD &&
         profitPct > 0.0)
      {
         exitNow = true;
         exitReason = "END_OF_DAY";
      }

      if(exitNow)
      {
         trade.SetTypeFillingBySymbol(g_symbol);

         bool requestOK =
            trade.PositionClose(ticket);

         uint retcode =
            trade.ResultRetcode();

         if(requestOK &&
            (retcode == TRADE_RETCODE_DONE ||
             retcode == TRADE_RETCODE_DONE_PARTIAL))
         {
            if(VerboseLog)
            {
               PrintFormat("[EXIT] %s #%I64u reason=%s profit=%.2f%%",
                           type == POSITION_TYPE_BUY
                           ? "BUY"
                           : "SELL",
                           ticket,
                           exitReason,
                           profitPct * 100.0);
            }
         }
         else
         {
            PrintFormat("[EXIT] FAILED #%I64u rc=%u %s",
                        ticket,
                        retcode,
                        trade.ResultRetcodeDescription());
         }
      }
   }
}

//+------------------------------------------------------------------+
//| Entry signal                                                     |
//| Uses CLOSED candles only.                                       |
//+------------------------------------------------------------------+
void CheckForEntry(const double atrVal)
{
   if(atrVal <= 0.0)
      return;

   // ---------------------------------------------------------------
   // Data is taken from shift 1, not shift 0.
   // This means only completed candles are used.
   // ---------------------------------------------------------------
   double prices[];
   ArraySetAsSeries(prices, true);

   int requested =
      NashPeriod + 50;

   if(CopyClose(g_symbol,
                g_tf,
                1,
                requested,
                prices) < requested)
      return;

   long volumes[];
   ArraySetAsSeries(volumes, true);

   if(CopyTickVolume(g_symbol,
                     g_tf,
                     1,
                     requested,
                     volumes) < requested)
      return;

   double rsi[];
   ArraySetAsSeries(rsi, true);

   if(CopyBuffer(g_hRSI,
                 0,
                 1,
                 3,
                 rsi) < 3)
      return;

   double mean = 0.0;
   double stddev = 0.0;

   CalcMeanStdDev(prices,
                  NashPeriod,
                  mean,
                  stddev);

   double nashEq =
      mean;

   double bandWidth =
      stddev > 0.0
      ? stddev *
        NashDeviationMultiplier
      : 0.0;

   double lowerBand =
      nashEq - bandWidth;

   double upperBand =
      nashEq + bandWidth;

   bool inNash =
      prices[0] >= lowerBand &&
      prices[0] <= upperBand;

   double nashDev =
      stddev > 0.0
      ? (prices[0] - nashEq) /
        stddev
      : 0.0;

   bool herd =
      ((rsi[0] > 70.0 ||
        rsi[0] < 30.0) &&
       volumes[0] >
       (long)(volumes[1] * 1.5));

   double mfgScore =
      CalcMFGScore(prices,
                   volumes,
                   atrVal);

   double tpProb =
      SimTPProbability(prices,
                       rsi[0],
                       atrVal);

   double rsiScore =
      1.0 -
      MathAbs(rsi[0] - 50.0) /
      50.0;

   rsiScore =
      MathMax(0.0,
              MathMin(1.0,
                      rsiScore));

   double conf = 0.0;

   conf +=
      0.25 *
      MathMax(0.0,
              1.0 -
              MathAbs(nashDev) /
              5.0);

   conf +=
      0.20 *
      mfgScore;

   conf +=
      0.25 *
      tpProb;

   conf +=
      0.15 *
      rsiScore;

   conf +=
      0.15 *
      (herd ? 0.5 : 1.0);

   if(ValidateNashZone &&
      inNash)
   {
      conf *= 0.5;
   }

   if(VerboseLog)
   {
      PrintFormat("[SIGNAL] conf=%.3f nash=%.3f mfg=%.3f tp=%.3f RSI=%.1f inNash=%s",
                  conf,
                  nashDev,
                  mfgScore,
                  tpProb,
                  rsi[0],
                  inNash ? "Y" : "N");
   }

   if(conf < MinConfidenceEntry)
      return;

   if(ValidateNashZone &&
      inNash)
      return;

   int direction =
      prices[0] > nashEq
      ? ORDER_TYPE_BUY
      : ORDER_TYPE_SELL;

   if(!TradingAllowed(direction))
      return;

   int opposite =
      direction == ORDER_TYPE_BUY
      ? -1
      : 1;

   if(CloseOppositeOnSignal &&
      CountPositions(opposite) > 0)
   {
      ClosePositions(opposite);
   }

   MqlTick tick;

   if(!SymbolInfoTick(g_symbol, tick))
      return;

   double entryPrice =
      direction == ORDER_TYPE_BUY
      ? tick.ask
      : tick.bid;

   double sl = 0.0;
   double tp = 0.0;

   CalcSLTP(direction,
            entryPrice,
            atrVal,
            sl,
            tp);

   // IMPORTANT:
   // Lot sizing uses this final SL.
   double lots =
      CalcRiskLotSize(direction,
                      entryPrice,
                      sl);

   if(lots <= 0.0)
   {
      if(VerboseLog)
         Print("[RISK] Trade skipped: valid risk volume unavailable");

      return;
   }

   Print("============================================================");
   PrintFormat("[TRADE] %s %s %s confidence=%.3f",
               direction == ORDER_TYPE_BUY
               ? "BUY"
               : "SELL",
               g_symbol,
               EnumToString(g_tf),
               conf);

   PrintFormat("[TRADE] Entry=%.5f SL=%.5f TP=%.5f Lots=%.4f",
               entryPrice,
               sl,
               tp,
               lots);

   PrintFormat("[TRADE] Nash=%.3f MFG=%.3f TPScore=%.3f ATR=%.5f",
               nashDev,
               mfgScore,
               tpProb,
               atrVal);

   if(UsePercentSL)
   {
      PrintFormat("[RISK] Fixed percentage SL enabled: %.3f%%",
                  StopLossPercent);
   }
   else
   {
      PrintFormat("[RISK] ATR SL enabled: %.2f ATR",
                  ATRMultiplierSL);
   }

   if(g_discoveryComplete)
   {
      PrintFormat("[BROKER] StopLevel=%.0f FreezeLevel=%.0f points",
                  g_brokerProfile.min_stop_distance_pts,
                  g_brokerProfile.freeze_distance_pts);
   }

   Print("============================================================");

   if(OpenPosition(direction,
                   lots,
                   sl,
                   tp,
                   conf))
   {
      g_lastTradeTime =
         TimeCurrent();

      RecordTrade();

      LogTradeDecision(TimeCurrent(),
                       direction,
                       entryPrice,
                       sl,
                       tp,
                       lots,
                       conf,
                       nashDev,
                       inNash,
                       mfgScore,
                       tpProb);
   }
}

//+------------------------------------------------------------------+
//| Trade decision CSV log                                           |
//+------------------------------------------------------------------+
void LogTradeDecision(datetime time,
                      int direction,
                      double price,
                      double sl,
                      double tp,
                      double lots,
                      double confidence,
                      double nashDev,
                      bool inNash,
                      double mfgScore,
                      double tpProb)
{
   MqlDateTime dt;
   TimeToStruct(time, dt);

   string file =
      StringFormat("ai_game_trades_%04d%02d.csv",
                   dt.year,
                   dt.mon);

   int h =
      FileOpen(file,
               FILE_WRITE |
               FILE_CSV   |
               FILE_READ,
               ',');

   if(h == INVALID_HANDLE)
      return;

   if(FileSize(h) == 0)
   {
      FileWrite(h,
                "Time",
                "Direction",
                "Price",
                "SL",
                "TP",
                "Lots",
                "Confidence",
                "NashDeviation",
                "InNashZone",
                "MFGScore",
                "TPProbability");
   }

   FileSeek(h,
            0,
            SEEK_END);

   FileWrite(h,
             TimeToString(time,
                          TIME_DATE |
                          TIME_MINUTES |
                          TIME_SECONDS),
             direction == ORDER_TYPE_BUY
             ? "BUY"
             : "SELL",
             DoubleToString(price, _Digits),
             DoubleToString(sl, _Digits),
             DoubleToString(tp, _Digits),
             DoubleToString(lots, VolumeDigits()),
             DoubleToString(confidence, 4),
             DoubleToString(nashDev, 4),
             inNash ? "YES" : "NO",
             DoubleToString(mfgScore, 4),
             DoubleToString(tpProb, 4));

   FileClose(h);
}

//+------------------------------------------------------------------+
//| Status log                                                       |
//+------------------------------------------------------------------+
void LogStatus()
{
   double balance =
      AccountInfoDouble(ACCOUNT_BALANCE);

   double equity =
      AccountInfoDouble(ACCOUNT_EQUITY);

   double profit =
      AccountInfoDouble(ACCOUNT_PROFIT);

   double dailyLossPct = 0.0;

   if(g_dayStartEquity > 0.0)
   {
      dailyLossPct =
         (g_dayStartEquity - equity) /
         g_dayStartEquity *
         100.0;
   }

   double drawdownPct = 0.0;

   if(g_peakEquity > 0.0)
   {
      drawdownPct =
         (g_peakEquity - equity) /
         g_peakEquity *
         100.0;
   }

   Print("============================================================");
   PrintFormat("[STATUS] %s %s",
               TimeToString(TimeCurrent()),
               DayName(CurrentDayOfWeek()));

   PrintFormat("[STATUS] %s %s",
               g_symbol,
               EnumToString(g_tf));

   PrintFormat("[STATUS] Ticks=%d TotalTrades=%d",
               g_tickCounter,
               g_totalTrades);

   PrintFormat("[STATUS] Open=%d/%d Today=%d/%d Week=%d/%d",
               CountPositions(0),
               MaxOpenTrades,
               g_todayTrades,
               MaxTradesPerDay,
               g_weekTrades,
               MaxTradesPerWeek);

   PrintFormat("[STATUS] Balance=%.2f Equity=%.2f PnL=%.2f",
               balance,
               equity,
               profit);

   PrintFormat("[STATUS] DailyLoss=%.2f%% Drawdown=%.2f%%",
               dailyLossPct,
               drawdownPct);

   if(UsePercentSL)
   {
      PrintFormat("[STATUS] SL mode=FIXED_PERCENT %.3f%%",
                  StopLossPercent);
   }
   else
   {
      PrintFormat("[STATUS] SL mode=ATR %.2f",
                  ATRMultiplierSL);
   }

   if(g_discoveryComplete)
   {
      PrintFormat("[STATUS] StopLevel=%.0f FreezeLevel=%.0f",
                  g_brokerProfile.min_stop_distance_pts,
                  g_brokerProfile.freeze_distance_pts);
   }

   Print("============================================================");
}

//+------------------------------------------------------------------+
//| Trade transaction audit                                         |
//+------------------------------------------------------------------+
void OnTradeTransaction(const MqlTradeTransaction &trans,
                        const MqlTradeRequest &request,
                        const MqlTradeResult &result)
{
   if(trans.symbol != g_symbol)
      return;

   if(request.magic != MagicNumber &&
      trans.deal == 0)
      return;

   if(trans.deal > 0)
   {
      long entry =
         HistoryDealGetInteger(trans.deal,
                               DEAL_ENTRY);

      long type =
         HistoryDealGetInteger(trans.deal,
                               DEAL_TYPE);

      double volume =
         HistoryDealGetDouble(trans.deal,
                              DEAL_VOLUME);

      double price =
         HistoryDealGetDouble(trans.deal,
                              DEAL_PRICE);

      long dealMagic =
         HistoryDealGetInteger(trans.deal,
                               DEAL_MAGIC);

      if(dealMagic == MagicNumber)
      {
         PrintFormat("[TRANSACTION] deal=%I64u entry=%d type=%d volume=%.4f price=%.5f",
                     trans.deal,
                     entry,
                     type,
                     volume,
                     price);
      }
   }
}

//+------------------------------------------------------------------+
//| Initialization                                                   |
//+------------------------------------------------------------------+
int OnInit()
{
   Print("============================================================");
   PrintFormat("%s v4.0", ExpertName);
   Print("Production risk/execution upgrade");
   Print("============================================================");

   g_symbol =
      Symbol();

   g_tf =
      (ENUM_TIMEFRAMES)Period();

   trade.SetExpertMagicNumber(MagicNumber);
   trade.SetDeviationInPoints(Slippage);

   if(EnableAutoDiscovery)
   {
      if(!LoadBrokerProfile())
      {
         Print("[INIT] Broker profile could not be loaded");
         return INIT_FAILED;
      }
   }
   else
   {
      LoadBrokerProfile();
   }

   g_hRSI =
      iRSI(g_symbol,
           g_tf,
           14,
           PRICE_CLOSE);

   g_hATR =
      iATR(g_symbol,
           g_tf,
           14);

   if(g_hRSI == INVALID_HANDLE ||
      g_hATR == INVALID_HANDLE)
   {
      Print("[INIT] Failed to create indicator handles");
      return INIT_FAILED;
   }

   g_lastResetDay =
      CurrentDayStart();

   g_lastResetWeek =
      CurrentWeekStart();

   g_dayStartEquity =
      AccountInfoDouble(ACCOUNT_EQUITY);

   g_peakEquity =
      g_dayStartEquity;

   RebuildTradeCounters();

   if(!TerminalInfoInteger(TERMINAL_TRADE_ALLOWED))
   {
      Print("[WARN] Terminal auto-trading is disabled");
   }

   if(!MQLInfoInteger(MQL_TRADE_ALLOWED))
   {
      Print("[WARN] EA trading permission is disabled");
   }

   PrintFormat("[INIT] Symbol=%s TF=%s Magic=%d Risk=%.2f%% MaxOpen=%d",
               g_symbol,
               EnumToString(g_tf),
               MagicNumber,
               RiskPercent,
               MaxOpenTrades);

   PrintFormat("[INIT] SL=%s",
               UsePercentSL
               ? "FIXED PERCENT"
               : "ATR");

   if(UsePercentSL)
   {
      PrintFormat("[INIT] Fixed SL=%.3f%%",
                  StopLossPercent);
   }
   else
   {
      PrintFormat("[INIT] ATR SL=%.2f ATR",
                  ATRMultiplierSL);
   }

   Print("[INIT] Broker discovery uses symbol metadata only");
   Print("[INIT] No discovery orders are submitted");

   return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
//| Deinitialization                                                 |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   if(g_hRSI != INVALID_HANDLE)
      IndicatorRelease(g_hRSI);

   if(g_hATR != INVALID_HANDLE)
      IndicatorRelease(g_hATR);

   PrintFormat("[DEINIT] reason=%d ticks=%d trades=%d",
               reason,
               g_tickCounter,
               g_totalTrades);
}

//+------------------------------------------------------------------+
//| Main tick                                                        |
//+------------------------------------------------------------------+
void OnTick()
{
   g_tickCounter++;

   double atrBuf[2];
   double atrVal = 0.0;

   if(CopyBuffer(g_hATR,
                 0,
                 0,
                 2,
                 atrBuf) == 2)
   {
      // Current ATR value used for management.
      // Entry itself uses closed candle data.
      atrVal = atrBuf[1];
   }

   ManageTrailingStop(atrVal);

   if(LogEveryNTicks > 0 &&
      g_tickCounter % LogEveryNTicks == 1)
   {
      LogStatus();
   }

   datetime barTime =
      iTime(g_symbol,
            g_tf,
            0);

   if(barTime == 0)
      return;

   if(barTime == g_lastBarTime)
      return;

   g_lastBarTime =
      barTime;

   if(VerboseLog)
   {
      PrintFormat("---- [%s %s] NEW BAR %s tick=%d ----",
                  g_symbol,
                  EnumToString(g_tf),
                  TimeToString(barTime,
                               TIME_DATE |
                               TIME_MINUTES),
                  g_tickCounter);
   }

   ManageOpenPositions(atrVal);

   CheckForEntry(atrVal);
}
//+------------------------------------------------------------------+
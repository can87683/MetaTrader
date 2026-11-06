//+-----------------------------------------------------------------+
//|  AI_Trader_timesfm.mq5
//+-----------------------------------------------------------------+

#include <Trade\Trade.mqh>

//+------------------------------------------------------------------+
//| Input Parameters                                                 |
//+------------------------------------------------------------------+
input group "=== AI Model Settings ==="
input string   ModelFilename      = "google_timesfm-2_5.onnx";
input double   SignalThreshold    = 0.55;
input int      LookbackBars       = 60;
input bool     UseGPU             = true;

input group "=== Trade Execution ==="
input double   LotSize            = 1;
input bool     UseAutoLotPercent  = false;
input double   RiskPercent        = 1.0;
input int      MaxOpenTrades      = 10;          // Maximum simultaneous open trades
input int      MagicNumber        = 202401;

input group "=== Trade Limits ==="
input int      MaxTradesPerDay    = 10;          // Maximum trades per day (0 = unlimited)
input int      MaxTradesPerWeek   = 50;          // Maximum trades per week (0 = unlimited)
input bool     NoTradeOnFriday    = true;        // Block trading on Fridays
input bool     ResetCountersAtMidnight = true;   // Reset daily counters at midnight

input group "=== Stop Loss & Take Profit ==="
input bool     UsePercentSL       = true;
input double   StopLossPercent    = 5;
input double   StopLossPoints     = 200;
input bool     UsePercentTP       = true;
input double   TakeProfitPercent  = 100;
input double   TakeProfitPoints   = 10000;
input bool     UseTrailingStop    = false;
input double   TrailingStopPoints = 1000;

input group "=== Filters ==="
input bool     TradeNewBarOnly    = true;
input int      MinBarsBetweenTrades = 3;
input bool     CloseOppositeOnSignal = true;

input group "=== Debug Settings ==="
input bool     ShowInferenceDetails = true;
input int      LogEveryNTicks      = 100;

//+------------------------------------------------------------------+
//| Global Variables                                                   |
//+------------------------------------------------------------------+
CTrade          trade;
long            g_onnxHandle = INVALID_HANDLE;
bool            g_onnxLoaded = false;
string          g_symbol;
ENUM_TIMEFRAMES g_tf;
string          g_tfStr;
datetime        g_lastBarTime = 0;
int             g_lastTradeBar = -100;
int             g_tickCounter = 0;
int             g_totalTrades = 0;
double          g_lastSignal = 0.5;
double          g_lastInferenceTime = 0;

// Trade counting variables
int             g_todayTrades = 0;
int             g_thisWeekTrades = 0;
datetime        g_lastResetDate = 0;
datetime        g_lastResetWeek = 0;

//+------------------------------------------------------------------+
//| Timeframe to string                                               |
//+------------------------------------------------------------------+
string TFToString(ENUM_TIMEFRAMES tf)
{
   switch(tf)
   {
      case PERIOD_M1:  return "M1";
      case PERIOD_M5:  return "M5";
      case PERIOD_M15: return "M15";
      case PERIOD_M30: return "M30";
      case PERIOD_H1:  return "H1";
      case PERIOD_H4:  return "H4";
      case PERIOD_D1:  return "D1";
      case PERIOD_W1:  return "W1";
      case PERIOD_MN1: return "MN1";
      default:         return "UNKNOWN";
   }
}

//+------------------------------------------------------------------+
//| Get current day of week (0=Sunday, 1=Monday, ..., 6=Saturday)    |
//+------------------------------------------------------------------+
int GetDayOfWeek()
{
   MqlDateTime dt;
   TimeToStruct(TimeCurrent(), dt);
   return dt.day_of_week;
}

//+------------------------------------------------------------------+
//| Get day name from day of week                                     |
//+------------------------------------------------------------------+
string GetDayName(int dayOfWeek)
{
   switch(dayOfWeek)
   {
      case 0: return "Sunday";
      case 1: return "Monday";
      case 2: return "Tuesday";
      case 3: return "Wednesday";
      case 4: return "Thursday";
      case 5: return "Friday";
      case 6: return "Saturday";
      default: return "Unknown";
   }
}

//+------------------------------------------------------------------+
//| Check if we can trade based on day of week                        |
//+------------------------------------------------------------------+
bool IsTradingDay()
{
   if(!NoTradeOnFriday)
      return true;
   
   int dayOfWeek = GetDayOfWeek();
   // In MQL5, day_of_week: 0=Sunday, 1=Monday, 2=Tuesday, 3=Wednesday, 4=Thursday, 5=Friday, 6=Saturday
   bool isFriday = (dayOfWeek == 5);
   
   if(isFriday && NoTradeOnFriday)
   {
      PrintFormat("[LIMIT] ⚠️ No trading on Friday! (Day: %s)", GetDayName(dayOfWeek));
      return false;
   }
   
   return true;
}

//+------------------------------------------------------------------+
//| Reset daily trade counter if new day                             |
//+------------------------------------------------------------------+
void ResetDailyCounter()
{
   if(!ResetCountersAtMidnight)
      return;
   
   datetime now = TimeCurrent();
   MqlDateTime today;
   TimeToStruct(now, today);
   today.hour = 0;
   today.min = 0;
   today.sec = 0;
   datetime todayMidnight = StructToTime(today);
   
   if(g_lastResetDate == 0)
   {
      g_lastResetDate = todayMidnight;
      return;
   }
   
   if(todayMidnight != g_lastResetDate)
   {
      PrintFormat("[LIMIT] New day detected - Resetting daily trade counter");
      PrintFormat("  Previous day: %d trades | New day: %s",
                  g_todayTrades, TimeToString(todayMidnight));
      g_todayTrades = 0;
      g_lastResetDate = todayMidnight;
   }
}

//+------------------------------------------------------------------+
//| Reset weekly trade counter if new week                           |
//+------------------------------------------------------------------+
void ResetWeeklyCounter()
{
   datetime now = TimeCurrent();
   MqlDateTime dt;
   TimeToStruct(now, dt);
   
   // Calculate start of week (Monday midnight)
   int daysFromMonday = (dt.day_of_week + 6) % 7; // Convert to Monday-based (0=Monday)
   dt.day -= daysFromMonday;
   dt.hour = 0;
   dt.min = 0;
   dt.sec = 0;
   datetime weekStart = StructToTime(dt);
   
   if(g_lastResetWeek == 0)
   {
      g_lastResetWeek = weekStart;
      return;
   }
   
   if(weekStart != g_lastResetWeek)
   {
      PrintFormat("[LIMIT] New week detected - Resetting weekly trade counter");
      PrintFormat("  Previous week: %d trades | Week starting: %s",
                  g_thisWeekTrades, TimeToString(weekStart));
      g_thisWeekTrades = 0;
      g_lastResetWeek = weekStart;
   }
}

//+------------------------------------------------------------------+
//| Check trade limits before opening new trade                       |
//+------------------------------------------------------------------+
bool CheckTradeLimits()
{
   // Reset counters
   ResetDailyCounter();
   ResetWeeklyCounter();
   
   // Check daily limit
   if(MaxTradesPerDay > 0 && g_todayTrades >= MaxTradesPerDay)
   {
      PrintFormat("[LIMIT] Daily trade limit reached! Today: %d/%d trades - No more trades today",
                  g_todayTrades, MaxTradesPerDay);
      return false;
   }
   
   // Check weekly limit
   if(MaxTradesPerWeek > 0 && g_thisWeekTrades >= MaxTradesPerWeek)
   {
      PrintFormat("[LIMIT] Weekly trade limit reached! This week: %d/%d trades - No more trades this week",
                  g_thisWeekTrades, MaxTradesPerWeek);
      return false;
   }
   
   // Check if trading day
   if(!IsTradingDay())
      return false;
   
   return true;
}

//+------------------------------------------------------------------+
//| Record a new trade                                                |
//+------------------------------------------------------------------+
void RecordTrade()
{
   g_totalTrades++;
   g_todayTrades++;
   g_thisWeekTrades++;
   
   PrintFormat("[LIMIT] Trade recorded - Total: %d | Today: %d/%d | Week: %d/%d",
               g_totalTrades, g_todayTrades, MaxTradesPerDay, 
               g_thisWeekTrades, MaxTradesPerWeek);
}

//+------------------------------------------------------------------+
//| Get file path for model                                           |
//+------------------------------------------------------------------+
string GetModelPath()
{
   string data_path = TerminalInfoString(TERMINAL_DATA_PATH);
   string common_path = TerminalInfoString(TERMINAL_COMMONDATA_PATH);
   
   string locations[] = {
      data_path + "\\MQL5\\Files\\" + ModelFilename,
      common_path + "\\" + ModelFilename,
      ModelFilename
   };
   
   for(int i = 0; i < ArraySize(locations); i++)
   {
      if(FileIsExist(locations[i]))
      {
         PrintFormat("[ONNX] Found model at: %s", locations[i]);
         return locations[i];
      }
   }
   
   Print("[ONNX] Model not found! Expected: ", data_path, "\\MQL5\\Files\\", ModelFilename);
   return "";
}

//+------------------------------------------------------------------+
//| Load ONNX Model                                                   |
//+------------------------------------------------------------------+
bool LoadONNXModel()
{
   Print("============================================================");
   Print("[ONNX] Loading model for ", g_symbol, " ", g_tfStr);
   
   string model_path = GetModelPath();
   if(model_path == "")
      return false;
   
   int flags = ONNX_LOGLEVEL_INFO;
   if(!UseGPU)
      flags |= ONNX_USE_CPU_ONLY;
   
   g_onnxHandle = OnnxCreate(model_path, flags);
   
   if(g_onnxHandle == INVALID_HANDLE)
   {
      int error = GetLastError();
      PrintFormat("[ONNX] Failed to load model. Error: %d", error);
      return false;
   }
   
   Print("[ONNX] ✅ Model loaded successfully!");
   PrintFormat("[ONNX] Handle: %d | Inputs: %d | Outputs: %d", 
               g_onnxHandle, 
               OnnxGetInputCount(g_onnxHandle), 
               OnnxGetOutputCount(g_onnxHandle));
   
   return true;
}

//+------------------------------------------------------------------+
//| Prepare input features (normalized OHLCV)                         |
//+------------------------------------------------------------------+
bool PrepareFeatures(float &features[])
{
   MqlRates rates[];
   int copied = CopyRates(g_symbol, g_tf, 1, LookbackBars, rates);
   
   if(copied < LookbackBars)
   {
      PrintFormat("[ERROR] Need %d bars, got %d", LookbackBars, copied);
      return false;
   }
   
   float raw[];
   ArrayResize(raw, LookbackBars * 5);
   ArrayResize(features, LookbackBars * 5);
   
   for(int i = 0; i < LookbackBars; i++)
   {
      int idx = i * 5;
      raw[idx + 0] = (float)rates[i].open;
      raw[idx + 1] = (float)rates[i].high;
      raw[idx + 2] = (float)rates[i].low;
      raw[idx + 3] = (float)rates[i].close;
      raw[idx + 4] = (float)rates[i].tick_volume;
   }
   
   // Z-score normalization per feature
   for(int f = 0; f < 5; f++)
   {
      double mean = 0, std = 0;
      for(int i = 0; i < LookbackBars; i++)
         mean += raw[i * 5 + f];
      mean /= LookbackBars;
      
      for(int i = 0; i < LookbackBars; i++)
         std += MathPow(raw[i * 5 + f] - mean, 2);
      std = sqrt(std / LookbackBars);
      if(std < 1e-8) std = 1.0;
      
      for(int i = 0; i < LookbackBars; i++)
         features[i * 5 + f] = (float)((raw[i * 5 + f] - mean) / std);
   }
   
   return true;
}

//+------------------------------------------------------------------+
//| Run inference and get signal                                      |
//+------------------------------------------------------------------+
double GetSignal()
{
   if(g_onnxHandle == INVALID_HANDLE)
      return 0.5;
   
   float features[];
   if(!PrepareFeatures(features))
      return 0.5;
   
   long input_shape[] = {1, LookbackBars, 5};
   if(!OnnxSetInputShape(g_onnxHandle, 0, input_shape))
   {
      Print("[ERROR] Failed to set input shape");
      return 0.5;
   }
   
   long output_shape[] = {1, 1};
   if(!OnnxSetOutputShape(g_onnxHandle, 0, output_shape))
   {
      Print("[ERROR] Failed to set output shape");
      return 0.5;
   }
   
   uint start = GetTickCount();
   float output[];
   bool success = OnnxRun(g_onnxHandle, ONNX_DEFAULT, features, output);
   uint elapsed = GetTickCount() - start;
   g_lastInferenceTime = elapsed;
   
   if(!success)
   {
      PrintFormat("[ERROR] Inference failed: %d", GetLastError());
      return 0.5;
   }
   
   double signal = MathMax(0.0, MathMin(1.0, output[0]));
   
   if(ShowInferenceDetails)
      PrintFormat("[ONNX] Inference: %.1f ms | Signal: %.4f", elapsed, signal);
   
   return signal;
}

//+------------------------------------------------------------------+
//| Count open positions                                              |
//+------------------------------------------------------------------+
int CountPositions(int direction = 0)
{
   int count = 0;
   for(int i = 0; i < PositionsTotal(); i++)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket > 0)
      {
         if(PositionGetString(POSITION_SYMBOL) != g_symbol)
            continue;
         if(PositionGetInteger(POSITION_MAGIC) != MagicNumber)
            continue;
         
         if(direction == 0)
            count++;
         else if(direction == 1 && PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY)
            count++;
         else if(direction == -1 && PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_SELL)
            count++;
      }
   }
   return count;
}

//+------------------------------------------------------------------+
//| Close positions in direction                                      |
//+------------------------------------------------------------------+
void ClosePositions(int direction)
{
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket > 0)
      {
         if(PositionGetString(POSITION_SYMBOL) != g_symbol)
            continue;
         if(PositionGetInteger(POSITION_MAGIC) != MagicNumber)
            continue;
         
         bool isBuy = (PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY);
         bool isSell = (PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_SELL);
         
         if((direction == 1 && isBuy) || (direction == -1 && isSell))
         {
            if(trade.PositionClose(ticket))
               PrintFormat("[CLOSE] Closed %s position %I64u at %.5f",
                           isBuy ? "BUY" : "SELL", ticket, PositionGetDouble(POSITION_PRICE_CURRENT));
         }
      }
   }
}

//+------------------------------------------------------------------+
//| Open trade                                                        |
//+------------------------------------------------------------------+
void OpenTrade(int direction, double signal)
{
   // Check trade limits before opening
   if(!CheckTradeLimits())
   {
      Print("[LIMIT] Trade blocked by limits");
      return;
   }
   
   int currentPositions = CountPositions(0);
   if(currentPositions >= MaxOpenTrades)
   {
      PrintFormat("[LIMIT] Max open trades reached! Current: %d/%d - No new trades",
                  currentPositions, MaxOpenTrades);
      return;
   }
   
   double price = (direction == 1) ? SymbolInfoDouble(g_symbol, SYMBOL_ASK) 
                                   : SymbolInfoDouble(g_symbol, SYMBOL_BID);
   
   double sl = 0, tp = 0;
   int digits = (int)SymbolInfoInteger(g_symbol, SYMBOL_DIGITS);
   
   if(UsePercentSL)
   {
      double pct = StopLossPercent / 100.0;
      sl = (direction == 1) ? price * (1.0 - pct) : price * (1.0 + pct);
   }
   else
   {
      double pts = StopLossPoints * SymbolInfoDouble(g_symbol, SYMBOL_POINT);
      sl = (direction == 1) ? price - pts : price + pts;
   }
   
   if(UsePercentTP)
   {
      double pct = TakeProfitPercent / 100.0;
      tp = (direction == 1) ? price * (1.0 + pct) : price * (1.0 - pct);
   }
   else
   {
      double pts = TakeProfitPoints * SymbolInfoDouble(g_symbol, SYMBOL_POINT);
      tp = (direction == 1) ? price + pts : price - pts;
   }
   
   sl = NormalizeDouble(sl, digits);
   tp = NormalizeDouble(tp, digits);
   
   string dirStr = (direction == 1) ? "BUY" : "SELL";
   int dayOfWeek = GetDayOfWeek();
   
   Print("╔════════════════════════════════════════════════════════════════╗");
   PrintFormat("║ [TRADE] %s SIGNAL ON %s %s", dirStr, g_symbol, g_tfStr);
   PrintFormat("║ Day: %s | Positions: %d/%d", GetDayName(dayOfWeek), currentPositions, MaxOpenTrades);
   PrintFormat("║ Daily Limit: %d/%d | Weekly Limit: %d/%d", 
               g_todayTrades, MaxTradesPerDay, g_thisWeekTrades, MaxTradesPerWeek);
   PrintFormat("║ Signal: %.4f | Threshold: %.2f", signal, SignalThreshold);
   PrintFormat("║ Entry: %.5f | SL: %.5f (%.2f%%) | TP: %.5f (%.2f%%)",
               price, sl, UsePercentSL ? StopLossPercent : 0, 
               tp, UsePercentTP ? TakeProfitPercent : 0);
   Print("╚════════════════════════════════════════════════════════════════╝");
   
   bool ok;
   if(direction == 1)
      ok = trade.Buy(LotSize, g_symbol, price, sl, tp, "TimesFM_" + g_tfStr);
   else
      ok = trade.Sell(LotSize, g_symbol, price, sl, tp, "TimesFM_" + g_tfStr);
   
   if(ok)
   {
      RecordTrade();
      g_lastTradeBar = (int)Bars(g_symbol, g_tf);
      PrintFormat("[SUCCESS] %s position opened on %s %s (Ticket: %I64u)", 
                  dirStr, g_symbol, g_tfStr, trade.ResultOrder());
   }
   else
   {
      PrintFormat("[FAILED] %s position failed. Error: %d", dirStr, GetLastError());
   }
}

//+------------------------------------------------------------------+
//| Manage trailing stop                                              |
//+------------------------------------------------------------------+
void ManageTrailingStop()
{
   if(!UseTrailingStop) return;
   
   double trailPts = TrailingStopPoints * SymbolInfoDouble(g_symbol, SYMBOL_POINT);
   double ptSize   = SymbolInfoDouble(g_symbol, SYMBOL_POINT);
   
   for(int i = 0; i < PositionsTotal(); i++)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket > 0)
      {
         if(PositionGetString(POSITION_SYMBOL) != g_symbol)
            continue;
         if(PositionGetInteger(POSITION_MAGIC) != MagicNumber)
            continue;
         
         double bid = SymbolInfoDouble(g_symbol, SYMBOL_BID);
         double ask = SymbolInfoDouble(g_symbol, SYMBOL_ASK);
         double sl = PositionGetDouble(POSITION_SL);
         
         if(PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY)
         {
            double newSL = bid - trailPts;
            if(newSL > sl + ptSize)
               trade.PositionModify(ticket, newSL, PositionGetDouble(POSITION_TP));
         }
         else if(PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_SELL)
         {
            double newSL = ask + trailPts;
            if(sl == 0.0 || newSL < sl - ptSize)
               trade.PositionModify(ticket, newSL, PositionGetDouble(POSITION_TP));
         }
      }
   }
}

//+------------------------------------------------------------------+
//| Log EA Status                                                     |
//+------------------------------------------------------------------+
void LogStatus()
{
   double balance = AccountInfoDouble(ACCOUNT_BALANCE);
   double equity = AccountInfoDouble(ACCOUNT_EQUITY);
   double profit = AccountInfoDouble(ACCOUNT_PROFIT);
   int positions = CountPositions(0);
   int dayOfWeek = GetDayOfWeek();
   
   Print("╔════════════════════════════════════════════════════════════════╗");
   Print("║ AI_TimesFM_EA STATUS REPORT                                       ║");
   Print("╠════════════════════════════════════════════════════════════════╣");
   PrintFormat("║ Time:        %s (%s)", TimeToString(TimeCurrent()), GetDayName(dayOfWeek));
   PrintFormat("║ Symbol/TF:   %s / %s", g_symbol, g_tfStr);
   PrintFormat("║ Ticks:       %d | Total Trades: %d", g_tickCounter, g_totalTrades);
   PrintFormat("║ Open Trades: %d / %d", positions, MaxOpenTrades);
   PrintFormat("║ Today:       %d / %d trades", g_todayTrades, MaxTradesPerDay);
   PrintFormat("║ This Week:   %d / %d trades", g_thisWeekTrades, MaxTradesPerWeek);
   PrintFormat("║ Balance:     %.2f | Equity: %.2f (%.2f)", balance, equity, profit);
   PrintFormat("║ Last Signal: %.4f (%s)", g_lastSignal,
               g_lastSignal >= SignalThreshold ? "BULL" :
               g_lastSignal <= (1.0 - SignalThreshold) ? "BEAR" : "NEUTRAL");
   PrintFormat("║ Last Inference: %.0f ms | Model: %s", g_lastInferenceTime, g_onnxLoaded ? "LOADED" : "DEMO");
   Print("╚════════════════════════════════════════════════════════════════╝");
}

//+------------------------------------------------------------------+
//| Expert initialization                                             |
//+------------------------------------------------------------------+
int OnInit()
{
   Print("╔════════════════════════════════════════════════════════════════╗");
   Print("║ AI_TimesFM_EA v6.20 - Enhanced with Trade Limits                 ║");
   Print("╚════════════════════════════════════════════════════════════════╝");
   
   g_symbol = Symbol();
   g_tf = Period();
   g_tfStr = TFToString(g_tf);
   
   PrintFormat("[INIT] Symbol: %s | Timeframe: %s", g_symbol, g_tfStr);
   PrintFormat("[INIT] MT5 Build: %d", TerminalInfoInteger(TERMINAL_BUILD));
   PrintFormat("[INIT] Model: %s", ModelFilename);
   PrintFormat("[INIT] Lookback: %d bars | Threshold: %.2f", LookbackBars, SignalThreshold);
   
   // Display trade limits
   Print("[LIMIT] Trade Limits Configuration:");
   PrintFormat("  Max Open Trades: %d", MaxOpenTrades);
   PrintFormat("  Max Trades Per Day: %s", MaxTradesPerDay > 0 ? IntegerToString(MaxTradesPerDay) : "Unlimited");
   PrintFormat("  Max Trades Per Week: %s", MaxTradesPerWeek > 0 ? IntegerToString(MaxTradesPerWeek) : "Unlimited");
   PrintFormat("  No Trade on Friday: %s", NoTradeOnFriday ? "YES" : "NO");
   PrintFormat("  Reset Counters at Midnight: %s", ResetCountersAtMidnight ? "YES" : "NO");
   
   // Check AutoTrading
   if(!TerminalInfoInteger(TERMINAL_TRADE_ALLOWED))
      Print("[WARN] ⚠️ AUTO-TRADING IS DISABLED! Click the green triangle.");
   else
      Print("[INIT] ✓ Auto-trading is ENABLED");
   
   // Load model
   g_onnxLoaded = LoadONNXModel();
   
   if(!g_onnxLoaded)
   {
      Print("[ERROR] Model not loaded - EA will run in DEMO MODE");
      Print("[INFO] Copy model to: ", TerminalInfoString(TERMINAL_DATA_PATH), "\\MQL5\\Files\\");
   }
   
   // Initialize trade
   trade.SetExpertMagicNumber(MagicNumber);
   trade.SetDeviationInPoints(20);
   
   // Initialize counters
   ResetDailyCounter();
   ResetWeeklyCounter();
   
   Print("╔════════════════════════════════════════════════════════════════╗");
   Print("║ EA RUNNING ON: ", g_symbol, " ", g_tfStr);
   Print("║ Waiting for new bar to generate signals...                     ║");
   Print("╚════════════════════════════════════════════════════════════════╝");
   
   return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
//| Expert deinitialization                                           |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   Print("╔════════════════════════════════════════════════════════════════╗");
   PrintFormat("║ EA STOPPED | Reason: %d | Ticks: %d | Trades: %d", 
               reason, g_tickCounter, g_totalTrades);
   PrintFormat("║ Final Stats - Today: %d | This Week: %d", g_todayTrades, g_thisWeekTrades);
   Print("╚════════════════════════════════════════════════════════════════╝");
   
   if(g_onnxHandle != INVALID_HANDLE)
   {
      OnnxRelease(g_onnxHandle);
      Print("[ONNX] Model released");
   }
}

//+------------------------------------------------------------------+
//| Expert tick function                                              |
//+------------------------------------------------------------------+
void OnTick()
{
   g_tickCounter++;
   
   // Manage trailing stop
   ManageTrailingStop();
   
   // Periodic status
   if(g_tickCounter % LogEveryNTicks == 1)
      LogStatus();
   
   // New bar detection
   datetime barTime = iTime(g_symbol, g_tf, 0);
   if(TradeNewBarOnly && barTime == g_lastBarTime)
      return;
   g_lastBarTime = barTime;
   
   // New bar log
   Print("────────────────────────────────────────────────────────────────");
   PrintFormat("[%s %s] NEW BAR: %s (Tick #%d)", 
               g_symbol, g_tfStr, TimeToString(barTime), g_tickCounter);
   
   // Min bars between trades filter
   if(MinBarsBetweenTrades > 0 && g_lastTradeBar > 0)
   {
      int barsSince = (int)Bars(g_symbol, g_tf) - g_lastTradeBar;
      if(barsSince < MinBarsBetweenTrades)
      {
         PrintFormat("[FILTER] %d bars since last trade, need %d - SKIPPING", 
                     barsSince, MinBarsBetweenTrades);
         return;
      }
   }
   
   // Get signal from model
   double signal = 0.5;
   
   if(g_onnxLoaded)
   {
      signal = GetSignal();
      g_lastSignal = signal;
   }
   else
   {
      // DEMO MODE - simulate signals
      signal = 0.5 + (MathRand() % 1000 - 500) / 1000.0;
      signal = MathMax(0.3, MathMin(0.7, signal));
      g_lastSignal = signal;
      Print("[DEMO] Simulated signal: ", signal);
   }
   
   // Determine direction
   int direction = 0;
   
   if(signal >= SignalThreshold)
   {
      direction = 1;
      PrintFormat("[SIGNAL] 🔺 BULLISH on %s %s | Signal: %.4f (Threshold: %.2f)", 
                  g_symbol, g_tfStr, signal, SignalThreshold);
   }
   else if(signal <= (1.0 - SignalThreshold))
   {
      direction = -1;
      PrintFormat("[SIGNAL] 🔻 BEARISH on %s %s | Signal: %.4f (Threshold: %.2f)", 
                  g_symbol, g_tfStr, signal, SignalThreshold);
   }
   else
   {
      PrintFormat("[SIGNAL] ⚪ NEUTRAL on %s %s | Signal: %.4f (Threshold: %.2f)", 
                  g_symbol, g_tfStr, signal, SignalThreshold);
      return;
   }
   
   // Check existing positions
   int currentPositions = CountPositions(direction);
   int oppositePositions = CountPositions(-direction);
   
   PrintFormat("[STATUS] Current %s: %d/%d | Opposite: %d", 
               direction == 1 ? "BUY" : "SELL", currentPositions, MaxOpenTrades, oppositePositions);
   
   // Close opposite positions if enabled
   if(CloseOppositeOnSignal && oppositePositions > 0)
   {
      Print("[ACTION] Closing opposite positions before opening new trade");
      ClosePositions(-direction);
   }
   
   // Open new position
   if(currentPositions < MaxOpenTrades)
   {
      OpenTrade(direction, signal);
   }
   else
   {
      PrintFormat("[INFO] Max open trades reached (%d/%d) on %s %s - No new trade",
                  currentPositions, MaxOpenTrades, g_symbol, g_tfStr);
   }
}
//+------------------------------------------------------------------+
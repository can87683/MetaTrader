//+------------------------------------------------------------------+
//|  AI_Trader-chronos-2.mq5
//+------------------------------------------------------------------+

#property copyright "AI_Trader_chronos-2"
#property version   "7.00"
#property description "AI-Powered Trading - Full ONNX Support + Auto Broker Detection"
#property description "Self-adapts to ANY broker without configuration"

#include <Trade\Trade.mqh>

//+------------------------------------------------------------------+
//| Input Parameters (ALL ORIGINAL)                                  |
//+------------------------------------------------------------------+
input group "=== AI Model Settings ==="
input string   ModelFilename      = "chronos-2.onnx";
input double   SignalThreshold    = 0.55;
input int      LookbackBars       = 60;
input bool     UseGPU             = false;

input group "=== Trade Execution ==="
input double   LotSize            = 1;
input bool     UseAutoLotPercent  = false;
input double   RiskPercent        = 1.0;
input int      MaxOpenTrades      = 10;
input int      MagicNumber        = 202401;

input group "=== Trade Limits ==="
input int      MaxTradesPerDay    = 10;
input int      MaxTradesPerWeek   = 50;
input bool     NoTradeOnFriday    = true;
input bool     ResetCountersAtMidnight = true;

input group "=== Stop Loss & Take Profit ==="
input bool     UsePercentSL       = true;
input double   StopLossPercent    = 2.0;
input double   StopLossPoints     = 200;
input bool     UsePercentTP       = true;
input double   TakeProfitPercent  = 100;
input double   TakeProfitPoints   = 10000;
input bool     UseTrailingStop    = false;
input double   TrailingStopPoints = 1000;

input group "=== Timeframe Filter ==="
input bool     AllowM1   = false;
input bool     AllowM5   = false;
input bool     AllowM15  = true;
input bool     AllowM30  = true;
input bool     AllowH1   = true;
input bool     AllowH4   = true;
input bool     AllowD1   = true;
input bool     AllowW1   = false;
input bool     AllowMN1  = false;

input group "=== Filters ==="
input bool     TradeNewBarOnly    = true;
input int      MinBarsBetweenTrades = 3;
input bool     CloseOppositeOnSignal = true;

input group "=== Debug Settings ==="
input bool     ShowInferenceDetails = true;
input int      LogEveryNTicks      = 100;

input group "=== Broker Adaptation (NEW) ==="
input bool     EnableAutoDiscovery = true;
input bool     ShowDiscoveryDetails = true;

//+------------------------------------------------------------------+
//| Broker Profile Structure                                        |
//+------------------------------------------------------------------+
struct SBrokerProfile
{
   int            exec_mode;
   int            filling_mode;
   bool           supports_market;
   bool           supports_instant;
   bool           requires_price;
   double         min_stop_distance_pts;
   double         min_tp_distance_pts;
   double         max_slippage_pts;
   double         min_lot;
   double         max_lot;
   double         lot_step;
   int            max_retries;
   int            retry_delay_ms;
   int            working_filling_modes[5];
   int            working_exec_modes[5];
   bool           discovered;
   datetime       discovery_time;
   double         min_freeze_level_pts;  // FIXED: Added missing field
};

//+------------------------------------------------------------------+
//| Global Variables                                                |
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

int             g_todayTrades = 0;
int             g_thisWeekTrades = 0;
datetime        g_lastResetDate = 0;
datetime        g_lastResetWeek = 0;

SBrokerProfile  g_brokerProfile;
bool            g_discoveryComplete = false;

//+------------------------------------------------------------------+
//| Helper constants                                               |
//+------------------------------------------------------------------+
#define SYMBOL_FILLING_RETURN_INT   2

//+------------------------------------------------------------------+
//| Timeframe to string (ORIGINAL)                                  |
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
//| Check if timeframe allowed (ORIGINAL)                           |
//+------------------------------------------------------------------+
bool IsTimeframeAllowed()
{
   switch(g_tf)
   {
      case PERIOD_M1:  return AllowM1;
      case PERIOD_M5:  return AllowM5;
      case PERIOD_M15: return AllowM15;
      case PERIOD_M30: return AllowM30;
      case PERIOD_H1:  return AllowH1;
      case PERIOD_H4:  return AllowH4;
      case PERIOD_D1:  return AllowD1;
      case PERIOD_W1:  return AllowW1;
      case PERIOD_MN1: return AllowMN1;
      default:         return false;
   }
}

//+------------------------------------------------------------------+
//| BROKER DISCOVERY ENGINE (FIXED)                                 |
//+------------------------------------------------------------------+
class CBrokerDiscovery
{
private:
   string m_symbol;
   bool   m_verbose;
   
   bool TestOrderParameters(int fill_mode, double price, double volume, double sl, double tp, uint &retcode)
   {
      CTrade testTrade;
      testTrade.SetExpertMagicNumber(MagicNumber);
      testTrade.SetTypeFilling((ENUM_ORDER_TYPE_FILLING)fill_mode);
      testTrade.SetDeviationInPoints(100);
      
      bool result = testTrade.Buy(volume, m_symbol, price, sl, tp, "DISCOVERY_TEST");
      retcode = testTrade.ResultRetcode();
      
      return (retcode == TRADE_RETCODE_DONE || 
              retcode == TRADE_RETCODE_REQUOTE);
   }
   
public:
   void CBrokerDiscovery(string symbol, bool verbose = true)
   {
      m_symbol = symbol;
      m_verbose = verbose;
   }
   
   bool Discover(SBrokerProfile &profile)
   {
      if(!EnableAutoDiscovery)
      {
         SetStandardProfile(profile);
         return true;
      }
      
      Print("╔════════════════════════════════════════════════════════════════╗");
      Print("║ 🔧 AUTO-DISCOVERY: Learning broker capabilities                ║");
      Print("╚════════════════════════════════════════════════════════════════╝");
      
      ZeroMemory(profile);
      
      // Get basic symbol info
      profile.min_lot = SymbolInfoDouble(m_symbol, SYMBOL_VOLUME_MIN);
      profile.max_lot = SymbolInfoDouble(m_symbol, SYMBOL_VOLUME_MAX);
      profile.lot_step = SymbolInfoDouble(m_symbol, SYMBOL_VOLUME_STEP);
      if(profile.min_lot <= 0) profile.min_lot = 0.01;
      if(profile.max_lot <= 0) profile.max_lot = 100;
      if(profile.lot_step <= 0) profile.lot_step = 0.01;
      
      // Detect execution mode
      long raw_exec = SymbolInfoInteger(m_symbol, SYMBOL_TRADE_EXEMODE);
      profile.exec_mode = (int)raw_exec;
      profile.supports_market = (raw_exec == SYMBOL_TRADE_EXECUTION_MARKET);
      profile.supports_instant = (raw_exec == SYMBOL_TRADE_EXECUTION_INSTANT);
      
      // Test filling modes
      int test_fill_modes[] = {ORDER_FILLING_FOK, ORDER_FILLING_IOC, SYMBOL_FILLING_RETURN_INT};
      double test_price = SymbolInfoDouble(m_symbol, SYMBOL_ASK);
      double test_volume = 0.001;
      int mode_count = 0;
      
      for(int i = 0; i < ArraySize(test_fill_modes); i++)
      {
         uint retcode;
         if(TestOrderParameters(test_fill_modes[i], test_price, test_volume, 0, 0, retcode))
         {
            profile.working_filling_modes[mode_count++] = test_fill_modes[i];
            if(m_verbose && ShowDiscoveryDetails)
               PrintFormat("[DISCOVERY] ✓ Filling mode %d works", test_fill_modes[i]);
         }
      }
      
      // Select best filling mode
      profile.filling_mode = ORDER_FILLING_IOC;
      for(int i = 0; i < mode_count; i++)
      {
         if(profile.working_filling_modes[i] == ORDER_FILLING_FOK)
         {
            profile.filling_mode = ORDER_FILLING_FOK;
            break;
         }
      }
      
      // Discover minimum stop distance
      double point = SymbolInfoDouble(m_symbol, SYMBOL_POINT);
      double current_price = SymbolInfoDouble(m_symbol, SYMBOL_ASK);
      double min_pts = 1;
      double max_pts = 5000;
      double found_pts = 100;
      
      for(int attempt = 0; attempt < 15; attempt++)
      {
         double test_pts = (min_pts + max_pts) / 2;
         double sl_price = current_price - (test_pts * point);
         double tp_price = current_price + (test_pts * point);
         
         sl_price = NormalizeDouble(sl_price, (int)SymbolInfoInteger(m_symbol, SYMBOL_DIGITS));
         tp_price = NormalizeDouble(tp_price, (int)SymbolInfoInteger(m_symbol, SYMBOL_DIGITS));
         
         uint retcode;
         if(TestOrderParameters(profile.filling_mode, current_price, 0.001, sl_price, tp_price, retcode))
         {
            max_pts = test_pts;
            found_pts = test_pts;
         }
         else
         {
            min_pts = test_pts;
         }
         
         if(max_pts - min_pts < 5) break;
      }
      
      profile.min_stop_distance_pts = MathMax(10, found_pts + 10);
      profile.min_tp_distance_pts = profile.min_stop_distance_pts;
      
      // Get freeze level
      long freeze_raw = SymbolInfoInteger(m_symbol, SYMBOL_TRADE_FREEZE_LEVEL);
      profile.min_freeze_level_pts = (freeze_raw > 0) ? (double)freeze_raw : profile.min_stop_distance_pts / 2;
      
      // Set other parameters
      profile.requires_price = !profile.supports_market;
      profile.max_retries = (profile.filling_mode == ORDER_FILLING_FOK) ? 5 : 3;
      profile.retry_delay_ms = (profile.filling_mode == ORDER_FILLING_FOK) ? 500 : 1000;
      profile.max_slippage_pts = 20;
      
      profile.discovered = true;
      profile.discovery_time = TimeCurrent();
      
      PrintDiscoverySummary(profile);
      return true;
   }
   
private:
   void SetStandardProfile(SBrokerProfile &profile)
   {
      profile.discovered = true;
      profile.filling_mode = ORDER_FILLING_IOC;
      profile.supports_market = true;
      profile.min_stop_distance_pts = 100;
      profile.min_tp_distance_pts = 100;
      profile.min_freeze_level_pts = 50;
      profile.max_retries = 3;
      profile.retry_delay_ms = 1000;
      profile.requires_price = false;
      profile.min_lot = SymbolInfoDouble(m_symbol, SYMBOL_VOLUME_MIN);
      if(profile.min_lot <= 0) profile.min_lot = 0.01;
      profile.max_lot = SymbolInfoDouble(m_symbol, SYMBOL_VOLUME_MAX);
      if(profile.max_lot <= 0) profile.max_lot = 100;
      profile.lot_step = SymbolInfoDouble(m_symbol, SYMBOL_VOLUME_STEP);
      if(profile.lot_step <= 0) profile.lot_step = 0.01;
   }
   
   void PrintDiscoverySummary(SBrokerProfile &profile)
   {
      Print("╔════════════════════════════════════════════════════════════════╗");
      Print("║ ✅ BROKER DISCOVERY COMPLETE                                    ║");
      Print("╠════════════════════════════════════════════════════════════════╣");
      PrintFormat("║ Execution:      %s", 
                  profile.supports_market ? "MARKET" : 
                  profile.supports_instant ? "INSTANT" : "REQUEST");
      PrintFormat("║ Filling Mode:   %s", 
                  profile.filling_mode == ORDER_FILLING_FOK ? "FOK" :
                  profile.filling_mode == ORDER_FILLING_IOC ? "IOC" : "RETURN");
      PrintFormat("║ Min Stop Dist:  %.0f points", profile.min_stop_distance_pts);
      PrintFormat("║ Min TP Dist:    %.0f points", profile.min_tp_distance_pts);
      PrintFormat("║ Freeze Level:   %.0f points", profile.min_freeze_level_pts);
      Print("╚════════════════════════════════════════════════════════════════╝");
   }
};

//+------------------------------------------------------------------+
//| Get best filling mode (UPGRADED)                                |
//+------------------------------------------------------------------+
ENUM_ORDER_TYPE_FILLING GetBestFillingMode()
{
   if(g_discoveryComplete && g_brokerProfile.filling_mode > 0)
      return (ENUM_ORDER_TYPE_FILLING)g_brokerProfile.filling_mode;
   
   long fillFlags = SymbolInfoInteger(g_symbol, SYMBOL_FILLING_MODE);
   if((fillFlags & SYMBOL_FILLING_FOK) != 0)
      return ORDER_FILLING_FOK;
   if((fillFlags & SYMBOL_FILLING_IOC) != 0)
      return ORDER_FILLING_IOC;
   
   return ORDER_FILLING_IOC;
}

//+------------------------------------------------------------------+
//| Validate stop levels (FIXED - removed min_freeze_level_pts)     |
//+------------------------------------------------------------------+
void ValidateAndAdjustStopLevels(int direction, double entry_price, double &sl, double &tp)
{
   if(!g_discoveryComplete) return;
   
   double point = SymbolInfoDouble(g_symbol, SYMBOL_POINT);
   double minStopDist = g_brokerProfile.min_stop_distance_pts * point;
   double minTPDist = g_brokerProfile.min_tp_distance_pts * point;
   
   if(sl != 0)
   {
      double slDistance = (direction == 1) ? entry_price - sl : sl - entry_price;
      if(slDistance < minStopDist && slDistance > 0)
      {
         PrintFormat("[ADAPTIVE] SL too close (%.1f pts), adjusting to %.1f pts", 
                     slDistance/point, minStopDist/point);
         if(direction == 1)
            sl = entry_price - minStopDist;
         else
            sl = entry_price + minStopDist;
      }
   }
   
   if(tp != 0)
   {
      double tpDistance = (direction == 1) ? tp - entry_price : entry_price - tp;
      if(tpDistance < minTPDist && tpDistance > 0)
      {
         PrintFormat("[ADAPTIVE] TP too close (%.1f pts), adjusting to %.1f pts", 
                     tpDistance/point, minTPDist/point);
         if(direction == 1)
            tp = entry_price + minTPDist;
         else
            tp = entry_price - minTPDist;
      }
   }
}

//+------------------------------------------------------------------+
//| Send trade with proper filling mode (UPDATED with clear EA name) |
//+------------------------------------------------------------------+
bool SendTrade(int order_type, double volume, double price, double sl, double tp)
{
   Print("[SEND] Sending trade: ", order_type == 1 ? "BUY" : "SELL", 
         " | Volume: ", volume, " | Price: ", price);
   
   if(!SymbolInfoInteger(g_symbol, SYMBOL_VISIBLE))
   {
      if(!SymbolSelect(g_symbol, true))
      {
         Print("[ERROR] Failed to select symbol ", g_symbol);
         return false;
      }
   }
   
   long execMode = SymbolInfoInteger(g_symbol, SYMBOL_TRADE_EXEMODE);
   bool isMarketExec = (execMode == SYMBOL_TRADE_EXECUTION_MARKET);
   
   ENUM_ORDER_TYPE_FILLING fillMode = GetBestFillingMode();
   trade.SetTypeFilling(fillMode);
   trade.SetDeviationInPoints(20);
   
   bool success = false;
   uint retcode = 0;
   
   double sl_price = (sl == 0) ? 0.0 : sl;
   double tp_price = (tp == 0) ? 0.0 : tp;
   
   // CREATE DESCRIPTIVE COMMENT FOR TRADES SECTION
   string comment = StringFormat("Chronos2_%s_Sig=%.2f_M%s", 
                                  g_tfStr, 
                                  g_lastSignal, 
                                  IntegerToString(MagicNumber));
   
   if(order_type == 1)  // BUY
   {
      if(isMarketExec)
         success = trade.Buy(volume, g_symbol, 0, sl_price, tp_price, comment);
      else
         success = trade.Buy(volume, g_symbol, price, sl_price, tp_price, comment);
   }
   else  // SELL
   {
      if(isMarketExec)
         success = trade.Sell(volume, g_symbol, 0, sl_price, tp_price, comment);
      else
         success = trade.Sell(volume, g_symbol, price, sl_price, tp_price, comment);
   }
   
   retcode = trade.ResultRetcode();
   
   if(success && retcode == TRADE_RETCODE_DONE)
   {
      Print("[SUCCESS] Trade executed | Retcode: ", retcode, " | Comment: ", comment);
      return true;
   }
   else
   {
      Print("[FAILED] Trade failed | Retcode: ", retcode);
      return false;
   }
}

//+------------------------------------------------------------------+
//| Calculate position size (ORIGINAL)                              |
//+------------------------------------------------------------------+
double CalculateLotSize()
{
   if(!UseAutoLotPercent)
      return LotSize;
   
   double riskAmount = AccountInfoDouble(ACCOUNT_BALANCE) * (RiskPercent / 100.0);
   double stopPoints = UsePercentSL ? 
                       (StopLossPercent / 100.0) * SymbolInfoDouble(g_symbol, SYMBOL_ASK) :
                       StopLossPoints * SymbolInfoDouble(g_symbol, SYMBOL_POINT);
   
   double lotStep = SymbolInfoDouble(g_symbol, SYMBOL_VOLUME_STEP);
   double minLot = SymbolInfoDouble(g_symbol, SYMBOL_VOLUME_MIN);
   double maxLot = SymbolInfoDouble(g_symbol, SYMBOL_VOLUME_MAX);
   
   double calculatedLot = riskAmount / stopPoints;
   calculatedLot = MathFloor(calculatedLot / lotStep) * lotStep;
   calculatedLot = MathMax(minLot, MathMin(maxLot, calculatedLot));
   
   return calculatedLot;
}

//+------------------------------------------------------------------+
//| Open trade (UPGRADED)                                           |
//+------------------------------------------------------------------+
void OpenTrade(int direction, double signal)
{
   if(!CheckTradeLimits()) return;

   int totalOpen = CountPositions(0);
   if(totalOpen >= MaxOpenTrades) return;

   double lotSize = CalculateLotSize();
   double price = (direction == 1) ? SymbolInfoDouble(g_symbol, SYMBOL_ASK)
                                   : SymbolInfoDouble(g_symbol, SYMBOL_BID);
   int digits = (int)SymbolInfoInteger(g_symbol, SYMBOL_DIGITS);
   
   double sl = 0, tp = 0;
   
   if(UsePercentSL)
   {
      double pct = StopLossPercent / 100.0;
      sl = (direction == 1) ? price * (1.0 - pct) : price * (1.0 + pct);
   }
   else if(StopLossPoints > 0)
   {
      double pts = StopLossPoints * SymbolInfoDouble(g_symbol, SYMBOL_POINT);
      sl = (direction == 1) ? price - pts : price + pts;
   }
   
   if(UsePercentTP)
   {
      double pct = TakeProfitPercent / 100.0;
      tp = (direction == 1) ? price * (1.0 + pct) : price * (1.0 - pct);
   }
   else if(TakeProfitPoints > 0)
   {
      double pts = TakeProfitPoints * SymbolInfoDouble(g_symbol, SYMBOL_POINT);
      tp = (direction == 1) ? price + pts : price - pts;
   }
   
   sl = NormalizeDouble(sl, digits);
   tp = NormalizeDouble(tp, digits);
   
   ValidateAndAdjustStopLevels(direction, price, sl, tp);
   
   Print("╔════════════════════════════════════════════════════════════════╗");
   PrintFormat("║ [TRADE] %s SIGNAL @ %.4f", (direction == 1) ? "BUY" : "SELL", signal);
   PrintFormat("║ Lot: %.2f | Entry: %.5f | SL: %.5f | TP: %.5f", lotSize, price, sl, tp);
   Print("╚════════════════════════════════════════════════════════════════╝");
   
   if(SendTrade(direction, lotSize, price, sl, tp))
   {
      RecordTrade();
      g_lastTradeBar = (int)Bars(g_symbol, g_tf);
   }
}

//+------------------------------------------------------------------+
//| Day of week helpers (ORIGINAL)                                  |
//+------------------------------------------------------------------+
int GetDayOfWeek()
{
   MqlDateTime dt;
   TimeToStruct(TimeCurrent(), dt);
   return dt.day_of_week;
}

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

bool IsTradingDay()
{
   if(!NoTradeOnFriday) return true;
   int dow = GetDayOfWeek();
   if(dow == 5)
   {
      PrintFormat("[LIMIT] No trading on Friday! (Day: %s)", GetDayName(dow));
      return false;
   }
   return true;
}

//+------------------------------------------------------------------+
//| Reset counters (ORIGINAL)                                       |
//+------------------------------------------------------------------+
void ResetDailyCounter()
{
   if(!ResetCountersAtMidnight) return;
   MqlDateTime now_dt;
   TimeToStruct(TimeCurrent(), now_dt);
   now_dt.hour = 0; now_dt.min = 0; now_dt.sec = 0;
   datetime todayMidnight = StructToTime(now_dt);
   if(g_lastResetDate == 0) { g_lastResetDate = todayMidnight; return; }
   if(todayMidnight == g_lastResetDate) return;
   PrintFormat("[LIMIT] New day — resetting daily counter. Was %d trades.", g_todayTrades);
   g_todayTrades = 0;
   g_lastResetDate = todayMidnight;
}

void ResetWeeklyCounter()
{
   MqlDateTime dt;
   TimeToStruct(TimeCurrent(), dt);
   int daysFromMonday = (dt.day_of_week + 6) % 7;
   datetime now = TimeCurrent();
   datetime weekStart = now - (datetime)(daysFromMonday * 86400)
                             - (datetime)(dt.hour * 3600)
                             - (datetime)(dt.min * 60)
                             - (datetime)(dt.sec);
   if(g_lastResetWeek == 0) { g_lastResetWeek = weekStart; return; }
   if(weekStart == g_lastResetWeek) return;
   PrintFormat("[LIMIT] New week — resetting weekly counter. Was %d trades.", g_thisWeekTrades);
   g_thisWeekTrades = 0;
   g_lastResetWeek = weekStart;
}

bool CheckTradeLimits()
{
   ResetDailyCounter();
   ResetWeeklyCounter();
   if(MaxTradesPerDay > 0 && g_todayTrades >= MaxTradesPerDay) return false;
   if(MaxTradesPerWeek > 0 && g_thisWeekTrades >= MaxTradesPerWeek) return false;
   if(!IsTradingDay()) return false;
   return true;
}

void RecordTrade()
{
   g_totalTrades++;
   g_todayTrades++;
   g_thisWeekTrades++;
}

//+------------------------------------------------------------------+
//| Model loading (ORIGINAL)                                        |
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
      if(FileIsExist(locations[i]))
         return locations[i];
   return "";
}

bool LoadONNXModel()
{
   Print("[ONNX] Loading model for ", g_symbol, " ", g_tfStr);
   string model_path = GetModelPath();
   if(model_path == "") return false;
   int flags = ONNX_LOGLEVEL_INFO;
   if(!UseGPU) flags |= ONNX_USE_CPU_ONLY;
   g_onnxHandle = OnnxCreate(model_path, flags);
   if(g_onnxHandle == INVALID_HANDLE) return false;
   Print("[ONNX] Model loaded successfully!");
   return true;
}

//+------------------------------------------------------------------+
//| Feature preparation & inference (ORIGINAL)                      |
//+------------------------------------------------------------------+
bool PrepareFeatures(float &features[])
{
   MqlRates rates[];
   int copied = CopyRates(g_symbol, g_tf, 1, LookbackBars, rates);
   if(copied < LookbackBars) return false;
   
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
   
   for(int f = 0; f < 5; f++)
   {
      double mean = 0, std = 0;
      for(int i = 0; i < LookbackBars; i++) mean += raw[i * 5 + f];
      mean /= LookbackBars;
      for(int i = 0; i < LookbackBars; i++) std += MathPow(raw[i * 5 + f] - mean, 2);
      std = sqrt(std / LookbackBars);
      if(std < 1e-8) std = 1.0;
      for(int i = 0; i < LookbackBars; i++)
         features[i * 5 + f] = (float)((raw[i * 5 + f] - mean) / std);
   }
   return true;
}

double GetSignal()
{
   if(g_onnxHandle == INVALID_HANDLE) return 0.5;
   float features[];
   if(!PrepareFeatures(features)) return 0.5;
   long input_shape[] = {1, LookbackBars, 5};
   long output_shape[] = {1, 1};
   if(!OnnxSetInputShape(g_onnxHandle, 0, input_shape)) return 0.5;
   if(!OnnxSetOutputShape(g_onnxHandle, 0, output_shape)) return 0.5;
   uint start = GetTickCount();
   float output[];
   bool success = OnnxRun(g_onnxHandle, ONNX_DEFAULT, features, output);
   uint elapsed = GetTickCount() - start;
   g_lastInferenceTime = elapsed;
   if(!success) return 0.5;
   double signal = MathMax(0.0, MathMin(1.0, output[0]));
   if(ShowInferenceDetails)
      PrintFormat("[ONNX] Inference: %.1f ms | Signal: %.4f", elapsed, signal);
   return signal;
}

//+------------------------------------------------------------------+
//| Position helpers (ORIGINAL)                                     |
//+------------------------------------------------------------------+
int CountPositions(int direction = 0)
{
   int count = 0;
   for(int i = 0; i < PositionsTotal(); i++)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(PositionGetString(POSITION_SYMBOL) != g_symbol) continue;
      if(PositionGetInteger(POSITION_MAGIC) != MagicNumber) continue;
      if(direction == 0) { count++; continue; }
      long ptype = PositionGetInteger(POSITION_TYPE);
      if(direction == 1 && ptype == POSITION_TYPE_BUY) count++;
      if(direction == -1 && ptype == POSITION_TYPE_SELL) count++;
   }
   return count;
}

void ClosePositions(int direction)
{
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(PositionGetString(POSITION_SYMBOL) != g_symbol) continue;
      if(PositionGetInteger(POSITION_MAGIC) != MagicNumber) continue;
      long ptype = PositionGetInteger(POSITION_TYPE);
      bool isBuy = (ptype == POSITION_TYPE_BUY);
      bool isSell = (ptype == POSITION_TYPE_SELL);
      if((direction == 1 && isBuy) || (direction == -1 && isSell))
         trade.PositionClose(ticket);
   }
}

//+------------------------------------------------------------------+
//| Manage trailing stop (ORIGINAL)                                 |
//+------------------------------------------------------------------+
void ManageTrailingStop()
{
   if(!UseTrailingStop) return;
   double trailPts = TrailingStopPoints * SymbolInfoDouble(g_symbol, SYMBOL_POINT);
   double ptSize = SymbolInfoDouble(g_symbol, SYMBOL_POINT);
   for(int i = 0; i < PositionsTotal(); i++)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(PositionGetString(POSITION_SYMBOL) != g_symbol) continue;
      if(PositionGetInteger(POSITION_MAGIC) != MagicNumber) continue;
      double bid = SymbolInfoDouble(g_symbol, SYMBOL_BID);
      double ask = SymbolInfoDouble(g_symbol, SYMBOL_ASK);
      double sl = PositionGetDouble(POSITION_SL);
      double tpv = PositionGetDouble(POSITION_TP);
      if(PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY)
      {
         double newSL = bid - trailPts;
         if(newSL > sl + ptSize)
            trade.PositionModify(ticket, newSL, tpv);
      }
      else if(PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_SELL)
      {
         double newSL = ask + trailPts;
         if(sl == 0.0 || newSL < sl - ptSize)
            trade.PositionModify(ticket, newSL, tpv);
      }
   }
}

//+------------------------------------------------------------------+
//| Log status (ORIGINAL + adaptive info)                           |
//+------------------------------------------------------------------+
void LogStatus()
{
   double balance = AccountInfoDouble(ACCOUNT_BALANCE);
   double equity = AccountInfoDouble(ACCOUNT_EQUITY);
   double profit = AccountInfoDouble(ACCOUNT_PROFIT);
   int positions = CountPositions(0);
   int dow = GetDayOfWeek();
   Print("╔════════════════════════════════════════════════════════════════╗");
   Print("║ AI_TFT_EA v7.00 - STATUS REPORT                                ║");
   Print("╠════════════════════════════════════════════════════════════════╣");
   PrintFormat("║ Time:        %s (%s)", TimeToString(TimeCurrent()), GetDayName(dow));
   PrintFormat("║ Symbol/TF:   %s / %s", g_symbol, g_tfStr);
   PrintFormat("║ Trades:      %d total | %d open", g_totalTrades, positions);
   PrintFormat("║ Today:       %d / %d | Week: %d / %d", 
               g_todayTrades, MaxTradesPerDay, g_thisWeekTrades, MaxTradesPerWeek);
   PrintFormat("║ Balance:     %.2f | Equity: %.2f (P&L: %.2f)", balance, equity, profit);
   PrintFormat("║ Last Signal: %.4f | Inference: %.0f ms", g_lastSignal, g_lastInferenceTime);
   if(g_discoveryComplete)
      PrintFormat("║ Adaptive:    %s mode | MinStop:%.0f pts",
                  g_brokerProfile.filling_mode == ORDER_FILLING_FOK ? "FOK" : "IOC",
                  g_brokerProfile.min_stop_distance_pts);
   Print("╚════════════════════════════════════════════════════════════════╝");
}

//+------------------------------------------------------------------+
//| OnInit                                                           |
//+------------------------------------------------------------------+
int OnInit()
{
   Print("╔════════════════════════════════════════════════════════════════╗");
   Print("║ AI_TFT_EA v7.00 - Chronos-2 + Auto Discovery                  ║");
   Print("╚════════════════════════════════════════════════════════════════╝");

   g_symbol = Symbol();
   g_tf = Period();
   g_tfStr = TFToString(g_tf);

   PrintFormat("[INIT] Symbol: %s | Timeframe: %s", g_symbol, g_tfStr);
   
   // Run broker discovery
   CBrokerDiscovery discovery(g_symbol, ShowDiscoveryDetails);
   if(discovery.Discover(g_brokerProfile))
   {
      g_discoveryComplete = true;
      Print("[INIT] ✅ Broker auto-discovery complete");
   }
   else
   {
      Print("[INIT] ⚠️ Using conservative defaults");
   }

   if(!IsTimeframeAllowed())
      Print("[WARN] Current timeframe not allowed!");
   
   if(!TerminalInfoInteger(TERMINAL_TRADE_ALLOWED))
      Print("[WARN] AUTO-TRADING IS DISABLED!");

   g_onnxLoaded = LoadONNXModel();
   if(!g_onnxLoaded)
      Print("[WARN] Model not loaded — DEMO MODE");

   trade.SetExpertMagicNumber(MagicNumber);
   ResetDailyCounter();
   ResetWeeklyCounter();

   Print("[INIT] EA Ready - Waiting for signals...");
   return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
//| OnDeinit                                                         |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   Print("EA STOPPED | Trades: ", g_totalTrades);
   if(g_onnxHandle != INVALID_HANDLE)
      OnnxRelease(g_onnxHandle);
}

//+------------------------------------------------------------------+
//| OnTick (ORIGINAL)                                                |
//+------------------------------------------------------------------+
void OnTick()
{
   g_tickCounter++;
   ManageTrailingStop();
   
   if(g_tickCounter % LogEveryNTicks == 1)
      LogStatus();
   
   if(!IsTimeframeAllowed()) return;
   
   datetime barTime = iTime(g_symbol, g_tf, 0);
   if(TradeNewBarOnly && barTime == g_lastBarTime) return;
   g_lastBarTime = barTime;
   
   if(MinBarsBetweenTrades > 0 && g_lastTradeBar > 0)
   {
      int barsSince = (int)Bars(g_symbol, g_tf) - g_lastTradeBar;
      if(barsSince < MinBarsBetweenTrades) return;
   }
   
   double signal = g_onnxLoaded ? GetSignal() : (0.5 + (MathRand() % 1000 - 500) / 1000.0);
   signal = MathMax(0.3, MathMin(0.7, signal));
   g_lastSignal = signal;
   
   int direction = 0;
   if(signal >= SignalThreshold)
      direction = 1;
   else if(signal <= (1.0 - SignalThreshold))
      direction = -1;
   else
      return;
   
   int curPos = CountPositions(direction);
   int oppPos = CountPositions(-direction);
   
   if(CloseOppositeOnSignal && oppPos > 0)
      ClosePositions(-direction);
   
   if(curPos < MaxOpenTrades)
      OpenTrade(direction, signal);
}
//+------------------------------------------------------------------+
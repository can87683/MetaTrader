//+------------------------------------------------------------------+
//|  AI_Trader_ML_LLM.mq5
//|  Copyright Su Nie | BSD-3C License | https://github.com/can87683
//+------------------------------------------------------------------+

#property strict
#property version "2.20"

#include <Trade/Trade.mqh>

//--- Percentage Trailing
input group "Percentage Trailing"
input double TriggerPercent = 5.0;
input double TrailPercent = 2.0;
input double StepPercent = 2.0;
input bool ManageAllSymbols = true;
input bool DrawTrailLine = true;

//--- Risk Management
input group "Risk Management"
input double DefaultStopLossPercent = 2.0;
input bool UseMaxSpreadFilter = true;
input double MaxSpreadPercent = 0.10;
input int MaxOpenPositions = 10;
input bool UseEquityDrawdownProtection = false;
input double MaxEquityDrawdownPercent = 10.0;
input bool ClosePositionsOnDrawdown = false;

CTrade trade;

double StartingEquity = 0.0;
bool DrawdownTriggered = false;

//+------------------------------------------------------------------+
int OnInit()
{
   if(TriggerPercent <= 0.0 ||
      TrailPercent <= 0.0 ||
      StepPercent <= 0.0 ||
      TrailPercent > TriggerPercent ||
      DefaultStopLossPercent <= 0.0 ||
      DefaultStopLossPercent >= 100.0 ||
      MaxSpreadPercent <= 0.0 ||
      MaxOpenPositions < 1 ||
      MaxEquityDrawdownPercent <= 0.0 ||
      MaxEquityDrawdownPercent >= 100.0)
   {
      Print("Invalid input parameters.");
      return INIT_PARAMETERS_INCORRECT;
   }

   StartingEquity = AccountInfoDouble(ACCOUNT_EQUITY);

   if(StartingEquity <= 0.0)
      return INIT_FAILED;

   trade.SetAsyncMode(false);

   return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
bool IsManagedPosition(const string symbol)
{
   if(ManageAllSymbols)
      return true;

   return symbol == _Symbol;
}

//+------------------------------------------------------------------+
bool CheckEquityDrawdown()
{
   if(!UseEquityDrawdownProtection)
      return true;

   if(DrawdownTriggered)
      return false;

   double equity = AccountInfoDouble(ACCOUNT_EQUITY);

   double drawdown =
      (StartingEquity - equity) / StartingEquity * 100.0;

   if(drawdown < MaxEquityDrawdownPercent)
      return true;

   DrawdownTriggered = true;

   Print("Risk Management: equity drawdown limit reached. Drawdown=",
         DoubleToString(drawdown, 2), "%");

   if(ClosePositionsOnDrawdown)
   {
      for(int i = PositionsTotal() - 1; i >= 0; i--)
      {
         ulong ticket = PositionGetTicket(i);

         if(ticket == 0)
            continue;

         if(!PositionSelectByTicket(ticket))
            continue;

         string symbol = PositionGetString(POSITION_SYMBOL);

         if(!IsManagedPosition(symbol))
            continue;

         if(!trade.PositionClose(ticket))
         {
            Print("Position close failed. Ticket=", ticket,
                  " Retcode=", trade.ResultRetcode(),
                  " ", trade.ResultRetcodeDescription());
         }
         else if(trade.ResultRetcode() != TRADE_RETCODE_DONE)
         {
            Print("Position close rejected. Ticket=", ticket,
                  " Retcode=", trade.ResultRetcode(),
                  " ", trade.ResultRetcodeDescription());
         }
      }
   }

   return false;
}

//+------------------------------------------------------------------+
bool RiskManagementAllowsTrailing(const string symbol)
{
   if(!CheckEquityDrawdown())
      return false;

   if(PositionsTotal() > MaxOpenPositions)
      return false;

   if(UseMaxSpreadFilter)
   {
      MqlTick tick;

      if(!SymbolInfoTick(symbol, tick))
         return false;

      if(tick.bid <= 0.0 || tick.ask <= 0.0)
         return false;

      double midPrice = (tick.bid + tick.ask) / 2.0;

      double spreadPercent =
         (tick.ask - tick.bid) / midPrice * 100.0;

      if(spreadPercent > MaxSpreadPercent)
         return false;
   }

   return true;
}

//+------------------------------------------------------------------+
double NormalizeStopPrice(const string symbol,
                          const long type,
                          const double requestedPrice,
                          const MqlTick &tick)
{
   double tickSize =
      SymbolInfoDouble(symbol, SYMBOL_TRADE_TICK_SIZE);

   double point =
      SymbolInfoDouble(symbol, SYMBOL_POINT);

   double stopLevel =
      (double)SymbolInfoInteger(symbol, SYMBOL_TRADE_STOPS_LEVEL)
      * point;

   double freezeLevel =
      (double)SymbolInfoInteger(symbol, SYMBOL_TRADE_FREEZE_LEVEL)
      * point;

   double minimumDistance = MathMax(stopLevel, freezeLevel);

   double price = requestedPrice;

   if(type == POSITION_TYPE_BUY)
   {
      price = MathMin(price, tick.bid - minimumDistance);
      price = MathFloor(price / tickSize) * tickSize;
   }
   else
   {
      price = MathMax(price, tick.ask + minimumDistance);
      price = MathCeil(price / tickSize) * tickSize;
   }

   return NormalizeDouble(
      price,
      (int)SymbolInfoInteger(symbol, SYMBOL_DIGITS)
   );
}

//+------------------------------------------------------------------+
bool AddDefaultStopLoss(const ulong ticket,
                        const string symbol,
                        const long type,
                        const double openPrice,
                        const double currentTP,
                        const MqlTick &tick)
{
   double requestedSL;

   if(type == POSITION_TYPE_BUY)
      requestedSL =
         openPrice * (1.0 - DefaultStopLossPercent / 100.0);
   else
      requestedSL =
         openPrice * (1.0 + DefaultStopLossPercent / 100.0);

   double newSL = NormalizeStopPrice(
      symbol, type, requestedSL, tick
   );

   bool validSL = false;

   if(type == POSITION_TYPE_BUY)
      validSL = newSL > 0.0 && newSL < tick.bid;
   else
      validSL = newSL > tick.ask;

   if(!validSL)
   {
      Print("Default S/L cannot be placed at a valid price. Ticket=",
            ticket, " Symbol=", symbol);
      return false;
   }

   if(!trade.PositionModify(ticket, newSL, currentTP))
   {
      Print("Default S/L modification failed. Ticket=", ticket,
            " Retcode=", trade.ResultRetcode(),
            " ", trade.ResultRetcodeDescription());
      return false;
   }

   uint retcode = trade.ResultRetcode();

   if(retcode != TRADE_RETCODE_DONE &&
      retcode != TRADE_RETCODE_NO_CHANGES)
   {
      Print("Default S/L rejected. Ticket=", ticket,
            " Retcode=", retcode,
            " ", trade.ResultRetcodeDescription());
      return false;
   }

   Print("Default S/L added. Ticket=", ticket,
         " Symbol=", symbol,
         " S/L=", DoubleToString(
            newSL,
            (int)SymbolInfoInteger(symbol, SYMBOL_DIGITS)
         ),
         " Distance=", DoubleToString(DefaultStopLossPercent, 2), "%");

   return true;
}

//+------------------------------------------------------------------+
void UpdateTrailLine(const ulong ticket,
                     const double stopPrice)
{
   if(!DrawTrailLine)
      return;

   string lineName = "CUSTOM_TS_" + IntegerToString((long)ticket);

   if(ObjectFind(0, lineName) < 0)
   {
      ObjectCreate(0, lineName, OBJ_HLINE, 0, 0, stopPrice);
      ObjectSetInteger(0, lineName, OBJPROP_SELECTABLE, false);
      ObjectSetInteger(0, lineName, OBJPROP_HIDDEN, true);
   }

   ObjectSetDouble(0, lineName, OBJPROP_PRICE, stopPrice);
}

//+------------------------------------------------------------------+
void ManageTrailing(const ulong ticket,
                    const string symbol,
                    const long type,
                    const double openPrice,
                    const double currentSL,
                    const double currentTP,
                    const MqlTick &tick)
{
   if(!RiskManagementAllowsTrailing(symbol))
      return;

   double favorablePercent;

   if(type == POSITION_TYPE_BUY)
      favorablePercent =
         (tick.bid - openPrice) / openPrice * 100.0;
   else
      favorablePercent =
         (openPrice - tick.ask) / openPrice * 100.0;

   if(favorablePercent < TriggerPercent)
      return;

   double steps = MathFloor(
      (favorablePercent - TriggerPercent) / StepPercent
   );

   double lockedPercent =
      TriggerPercent - TrailPercent + steps * StepPercent;

   if(lockedPercent <= 0.0)
      return;

   double requestedSL;

   if(type == POSITION_TYPE_BUY)
      requestedSL =
         openPrice * (1.0 + lockedPercent / 100.0);
   else
      requestedSL =
         openPrice * (1.0 - lockedPercent / 100.0);

   double newSL = NormalizeStopPrice(
      symbol, type, requestedSL, tick
   );

   if(newSL <= 0.0)
      return;

   if(type == POSITION_TYPE_BUY)
   {
      if(newSL >= tick.bid)
         return;

      if(currentSL != 0.0 && newSL <= currentSL)
         return;
   }
   else
   {
      if(newSL <= tick.ask)
         return;

      if(currentSL != 0.0 && newSL >= currentSL)
         return;
   }

   if(!trade.PositionModify(ticket, newSL, currentTP))
   {
      Print("Trailing SL modification failed. Ticket=", ticket,
            " Retcode=", trade.ResultRetcode(),
            " ", trade.ResultRetcodeDescription());
      return;
   }

   uint retcode = trade.ResultRetcode();

   if(retcode != TRADE_RETCODE_DONE &&
      retcode != TRADE_RETCODE_NO_CHANGES)
   {
      Print("Trailing SL rejected. Ticket=", ticket,
            " Retcode=", retcode,
            " ", trade.ResultRetcodeDescription());
      return;
   }

   UpdateTrailLine(ticket, newSL);

   Print("Trailing S/L updated. Ticket=", ticket,
         " Symbol=", symbol,
         " S/L=", DoubleToString(
            newSL,
            (int)SymbolInfoInteger(symbol, SYMBOL_DIGITS)
         ),
         " Locked percentage=", DoubleToString(lockedPercent, 2), "%");
}

//+------------------------------------------------------------------+
void OnTick()
{
   CheckEquityDrawdown();

   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);

      if(ticket == 0)
         continue;

      if(!PositionSelectByTicket(ticket))
         continue;

      string symbol = PositionGetString(POSITION_SYMBOL);

      if(!IsManagedPosition(symbol))
         continue;

      long type = PositionGetInteger(POSITION_TYPE);

      if(type != POSITION_TYPE_BUY &&
         type != POSITION_TYPE_SELL)
         continue;

      double openPrice =
         PositionGetDouble(POSITION_PRICE_OPEN);

      double currentSL =
         PositionGetDouble(POSITION_SL);

      double currentTP =
         PositionGetDouble(POSITION_TP);

      if(openPrice <= 0.0)
         continue;

      MqlTick tick;

      if(!SymbolInfoTick(symbol, tick))
         continue;

      if(tick.bid <= 0.0 || tick.ask <= 0.0)
         continue;

      // Repair missing S/L before evaluating trailing.
      if(currentSL == 0.0)
      {
         if(!AddDefaultStopLoss(
               ticket, symbol, type,
               openPrice, currentTP, tick))
            continue;

         if(!PositionSelectByTicket(ticket))
            continue;

         currentSL = PositionGetDouble(POSITION_SL);
         currentTP = PositionGetDouble(POSITION_TP);
      }

      ManageTrailing(
         ticket,
         symbol,
         type,
         openPrice,
         currentSL,
         currentTP,
         tick
      );
   }
}
//+------------------------------------------------------------------+

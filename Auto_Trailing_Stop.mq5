//+------------------------------------------------------------------+
//|  Auto_Trailing_Stop.mq5
//|  
//|  Copyright Su Nie | BSD-3C License | https://github.com/can87683
//|  
//+------------------------------------------------------------------+
//|  Two-layer design:                                               |
//|                                                                  |
//|  1. STOP LOSS (loss control)                                     |
//|     Fixed % from entry, set at/after open, invariant.            |
//|     Its job: bound the loss on a losing trade.                   |
//|     Its failure mode must be "never absent".                     |
//|                                                                  |
//|  2. PROFIT PROTECTION (floating-profit guard)                    |
//|     Engages only once the trade is in profit by TriggerPercent,  |
//|     then ratchets the S/L in StepPercent increments.             |
//|     Its job: convert floating profit into realized profit.       |
//|     It never operates in the loss region by construction.        |
//|                                                                  |
//|  Both write the same broker field (POSITION_SL), but they serve  |
//|  opposite intents and live in disjoint price regions. Keeping    |
//|  that distinction explicit prevents the two from being merged    |
//|  into one fuzzy "stop" that does neither job cleanly.            |
//+------------------------------------------------------------------+

#property strict
#property version "2.40"

#include <Trade/Trade.mqh>

//--- Profit Protection (guards floating profit, not loss)
input group "Profit Protection"
input double TriggerPercent = 5.0;
input double StepPercent = 0.5;
input bool ManageAllSymbols = true;
input bool DrawProfitGuardLine = true;

//--- Loss Control / Risk Management
input group "Loss Control / Risk Management"
input double DefaultStopLossPercent = 2.0;
input bool UseMaxSpreadFilter = true;
input double MaxSpreadPercent = 0.10;
input int MaxOpenPositions = 10;   // entry-side use only; does not gate profit protection
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
      StepPercent <= 0.0 ||
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
// Profit protection is allowed only when the account is not in a
// protected-drawdown state and the spread is not hostile. These are
// market-state conditions, not trade-count conditions.
bool ProfitProtectionAllowed(const string symbol)
{
   if(!CheckEquityDrawdown())
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
// Loss control: ensure the invariant loss boundary exists.
bool EnsureDefaultStopLoss(const ulong ticket,
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
void UpdateProfitGuardLine(const ulong ticket,
                           const double stopPrice)
{
   if(!DrawProfitGuardLine)
      return;

   string lineName = "PROFIT_GUARD_" + IntegerToString((long)ticket);

   if(ObjectFind(0, lineName) < 0)
   {
      ObjectCreate(0, lineName, OBJ_HLINE, 0, 0, stopPrice);
      ObjectSetInteger(0, lineName, OBJPROP_SELECTABLE, false);
      ObjectSetInteger(0, lineName, OBJPROP_HIDDEN, true);
   }

   ObjectSetDouble(0, lineName, OBJPROP_PRICE, stopPrice);
}

//+------------------------------------------------------------------+
// Profit protection: ratchet the S/L up behind favorable price.
// Engages only when the trade is already in profit by TriggerPercent.
void ManageProfitProtection(const ulong ticket,
                            const string symbol,
                            const long type,
                            const double openPrice,
                            const double currentSL,
                            const double currentTP,
                            const MqlTick &tick)
{
   if(!ProfitProtectionAllowed(symbol))
      return;

   double favorablePercent;

   if(type == POSITION_TYPE_BUY)
      favorablePercent =
         (tick.bid - openPrice) / openPrice * 100.0;
   else
      favorablePercent =
         (openPrice - tick.ask) / openPrice * 100.0;

   // Below trigger the trade is not yet winning: the default S/L
   // (loss control) governs entirely. Profit protection stays idle.
   if(favorablePercent < TriggerPercent)
      return;

   // Number of completed steps beyond the trigger.
   double steps = MathFloor(
      (favorablePercent - TriggerPercent) / StepPercent + 1e-9
   );

   // Lock the full distance reached above the trigger.
   double lockedPercent =
      TriggerPercent + steps * StepPercent;

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
      Print("Profit guard S/L modification failed. Ticket=", ticket,
            " Retcode=", trade.ResultRetcode(),
            " ", trade.ResultRetcodeDescription());
      return;
   }

   uint retcode = trade.ResultRetcode();

   if(retcode != TRADE_RETCODE_DONE &&
      retcode != TRADE_RETCODE_NO_CHANGES)
   {
      Print("Profit guard S/L rejected. Ticket=", ticket,
            " Retcode=", retcode,
            " ", trade.ResultRetcodeDescription());
      return;
   }

   UpdateProfitGuardLine(ticket, newSL);

   Print("Profit guard S/L updated. Ticket=", ticket,
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

      // Loss control first: repair a missing S/L before profit protection.
      if(currentSL == 0.0)
      {
         if(!EnsureDefaultStopLoss(
               ticket, symbol, type,
               openPrice, currentTP, tick))
            continue;

         if(!PositionSelectByTicket(ticket))
            continue;

         currentSL = PositionGetDouble(POSITION_SL);
         currentTP = PositionGetDouble(POSITION_TP);
      }

      ManageProfitProtection(
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
//+------------------------------------------------------------------+----------------------------+
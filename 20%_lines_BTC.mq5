//+---------------------------------------------+
//|  20%_lines_HK50.mq5                         |
//|  Copyright Su Nie                           |
//|  BSD-3-Clause License                       |
//|  https://www.github.com/can87683            |
//|                                             |
//+---------------------------------------------+
#property indicator_chart_window
#property indicator_buffers 0
#property indicator_plots   0

//--- Input parameters
input double  BaseValue = 10000;             // Base Price (0 = Auto-detect current price)
input double  PercentStep = 20;             // Percent Step (%)
input int     TotalLines = 50;              // Number of lines to draw
input int     LineWidth = 2;                // Line Width
input color   LineColor = clrRed;           // Line Color
input color   TextColor = clrYellow;        // Text Color (Yellow)
input int     TextDistanceFromRight = 60;   // Horizontal distance from right
input int     TextOffsetPixels = 0;         // Vertical distance above line
input int     FontSize = 10;                // Text Font Size

double        UsedBaseValue;

//+------------------------------------------------------------------+
//| Custom indicator initialization function                         |
//+------------------------------------------------------------------+
int OnInit()
{
    DeleteAllObjects();

    if(BaseValue <= 0)
        UsedBaseValue = SymbolInfoDouble(_Symbol, SYMBOL_BID);
    else
        UsedBaseValue = BaseValue;

    CreatePercentLines();
    UpdateLabelPositions();
    ChartRedraw(0);

    return(INIT_SUCCEEDED);
}

//+------------------------------------------------------------------+
//| Deinitialization                                                 |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
    DeleteAllObjects();
    ChartRedraw(0);
}

//+------------------------------------------------------------------+
//| OnCalculate                                                      |
//+------------------------------------------------------------------+
int OnCalculate(const int rates_total, const int prev_calculated, const datetime &time[],
                const double &open[], const double &high[], const double &low[],
                const double &close[], const long &tick_volume[], const long &volume[],
                const int &spread[])
{
    UpdateLabelPositions();
    return(rates_total);
}

//+------------------------------------------------------------------+
//| OnChartEvent                                                     |
//+------------------------------------------------------------------+
void OnChartEvent(const int id, const long &lparam, const double &dparam, const string &sparam)
{
    if(id == CHARTEVENT_CHART_CHANGE)
    {
        UpdateLabelPositions();
    }
}

//+------------------------------------------------------------------+
//| Create the objects                                               |
//+------------------------------------------------------------------+
void CreatePercentLines()
{
    // Fix: Use a valid time anchor instead of 0 to prevent MT5 rendering culling bugs
    datetime anchorTime = iTime(_Symbol, _Period, 0);
    if(anchorTime == 0) anchorTime = TimeCurrent();

    for(int i = 0; i < TotalLines; i++)
    {
        double multiplier = 1.0 + (PercentStep / 100.0) * i;
        double linePrice = UsedBaseValue * multiplier;

        string lineName  = "PLine_" + IntegerToString(i);
        string labelName = "PLabel_" + IntegerToString(i);

        if(ObjectCreate(0, lineName, OBJ_HLINE, 0, anchorTime, linePrice))
        {
            ObjectSetInteger(0, lineName, OBJPROP_COLOR, LineColor);
            ObjectSetInteger(0, lineName, OBJPROP_WIDTH, LineWidth);
            ObjectSetInteger(0, lineName, OBJPROP_BACK, true);
            ObjectSetInteger(0, lineName, OBJPROP_SELECTABLE, false);
        }

        if(ObjectCreate(0, labelName, OBJ_LABEL, 0, 0, 0))
        {
            ObjectSetString(0, labelName, OBJPROP_TEXT, DoubleToString(linePrice, _Digits));
            ObjectSetInteger(0, labelName, OBJPROP_COLOR, TextColor);
            ObjectSetInteger(0, labelName, OBJPROP_FONTSIZE, FontSize);
            ObjectSetString(0, labelName, OBJPROP_FONT, "Arial Bold");
            ObjectSetInteger(0, labelName, OBJPROP_CORNER, CORNER_RIGHT_UPPER);
            ObjectSetInteger(0, labelName, OBJPROP_ANCHOR, ANCHOR_RIGHT_LOWER);
            ObjectSetInteger(0, labelName, OBJPROP_SELECTABLE, false);
        }
    }
}

//+------------------------------------------------------------------+
//| Update position logic                                            |
//+------------------------------------------------------------------+
void UpdateLabelPositions()
{
    int x_pix, y_pix;
    datetime currentTime = iTime(_Symbol, _Period, 0);
    if(currentTime == 0) currentTime = TimeCurrent();

    for(int i = 0; i < TotalLines; i++)
    {
        string lineName  = "PLine_" + IntegerToString(i);
        string labelName = "PLabel_" + IntegerToString(i);

        if(ObjectFind(0, lineName) >= 0)
        {
            double price = ObjectGetDouble(0, lineName, OBJPROP_PRICE);

            if(ChartTimePriceToXY(0, 0, currentTime, price, x_pix, y_pix))
            {
                ObjectSetInteger(0, labelName, OBJPROP_XDISTANCE, TextDistanceFromRight);
                ObjectSetInteger(0, labelName, OBJPROP_YDISTANCE, y_pix - TextOffsetPixels);
                ObjectSetInteger(0, labelName, OBJPROP_HIDDEN, false);
            }
            else
            {
                // Hide label if coordinate conversion fails (e.g., price temporarily out of view)
                ObjectSetInteger(0, labelName, OBJPROP_HIDDEN, true);
            }
        }
    }
    ChartRedraw(0);
}

//+------------------------------------------------------------------+
//| Cleanup                                                          |
//+------------------------------------------------------------------+
void DeleteAllObjects()
{
    ObjectsDeleteAll(0, "PLine_");
    ObjectsDeleteAll(0, "PLabel_");
}
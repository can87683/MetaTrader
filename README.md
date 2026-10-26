# MetaTrader5 Connector
If free data is desired, MT5 is likely the best choice

This script fetches all Market Watch Symbols data to a folder/sub-folders for use by other scripts, e.g., Machine Learning training or synthetic data ingredients.

# 20% line price level target indicator
Various symbols scripts are provided

Any can be changed with the base price/value to fit with other symbols

# Auto_Trailing_Stop.mql5
scan any open trades with empty S/L value and fill with 98% of entry value

Broker server only follows S/L values. Trailing stop is a client side compute loop
Trailing Stop is guarding floating profit

It keeps raising Stop Loss hurdle, if market price moves favorably

Stop Loss is the only permanent loss guard against math asymmetry

Over 25% loss from entry price is more difficult to recoup

50% loss requires 100% profit to recoup flat

# Some EA/Onnx model pairs to show MT5 native Onnx inference driven trading

# Use
This script should be run in a Python venv with required dependencies

Do "python mt5_connector.py" will show required wheels in terminal/console

For instance, just do "pip install setproctitle" if crashes on "no module named setproctitle"


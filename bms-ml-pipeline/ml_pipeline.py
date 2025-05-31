import sqlite3
import pandas as pd
import numpy as np
from sklearn.model_selection import train_test_split
from xgboost import XGBRegressor
from sklearn.metrics import mean_squared_error, r2_score
import time

# Function to convert seconds to hours and minutes
def seconds_to_hm(seconds):
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    return f"{hours}h {minutes}m"

# Interval for updating predictions (in seconds)
update_interval = 300  # 5 minutes

while True:
    # Step 1: Connect to SQLite database
    conn = sqlite3.connect('battery_data.db')
    cursor = conn.cursor()

    # Step 2: Query recent data (e.g., last 7 days)
    query = "SELECT timestamp, voltage, soc, temperature, current FROM battery_data WHERE timestamp >= datetime('now', '-7 days')"
    df = pd.read_sql_query(query, conn)

    # Step 3: Get the latest data point for current state
    latest_data_query = "SELECT timestamp, voltage, soc, temperature, current FROM battery_data ORDER BY timestamp DESC LIMIT 1"
    latest_df = pd.read_sql_query(latest_data_query, conn)
    conn.close()

    # Step 4: Preprocess the data
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    df['soc'] = df['soc'].clip(upper=100.0)
    df['time_diff'] = df['timestamp'].diff().dt.total_seconds().fillna(0)
    df['soc_diff'] = df['soc'].diff().abs()
    df = df[(df['soc_diff'] <= 0.05) | (df['soc_diff'].isna())]
    df['voltage_slope'] = df['voltage'].diff() / df['time_diff']
    df['soc_slope'] = df['soc'].diff() / df['time_diff']
    df['voltage_slope'] = df['voltage_slope'].fillna(0)
    df['soc_slope'] = df['soc_slope'].fillna(0)

    # Smooth soc_slope with a longer rolling average (300 seconds)
    df['soc_slope'] = df['soc_slope'].rolling(window=300, min_periods=1).mean().fillna(0)

    # Compute time since last state change
    df['current_change'] = df['current'].diff().ne(0).cumsum()
    df['time_since_state_change'] = df.groupby('current_change')['time_diff'].cumsum()

    # Add SOC-based features
    df['soc_distance_to_full'] = 100.0 - df['soc']
    df['soc_distance_to_zero'] = df['soc']

    # Step 5: Calculate target (time to full charge/discharge in seconds)
    df['soc_slope'] = df['soc_slope'].replace(0, np.nan)
    df['time_to_full'] = np.where(df['current'] > 0, df['soc_distance_to_full'] / df['soc_slope'], np.nan)
    df['time_to_zero'] = np.where(df['current'] < 0, df['soc_distance_to_zero'] / abs(df['soc_slope']), np.nan)

    # Filter out invalid target values
    df = df.replace([np.inf, -np.inf], np.nan)

    # Separate datasets for charging and discharging
    charging_df = df[df['current'] > 0].dropna(subset=['time_to_full']).copy()
    discharging_df = df[df['current'] < 0].dropna(subset=['time_to_zero']).copy()

    # Step 6: Train models for charging and discharging
    features = ['voltage', 'soc', 'temperature', 'voltage_slope', 'soc_slope', 'time_since_state_change', 'soc_distance_to_full']

    # Charging model (time to full charge)
    if not charging_df.empty:
        X_charge = charging_df[features]
        y_charge = charging_df['time_to_full']
        X_train_c, X_test_c, y_train_c, y_test_c = train_test_split(X_charge, y_charge, test_size=0.2, random_state=42)
        charge_model = XGBRegressor(n_estimators=100, learning_rate=0.05, max_depth=5, reg_lambda=0.5, reg_alpha=0.05, random_state=42)
        charge_model.fit(X_train_c, y_train_c)
        y_pred_c = charge_model.predict(X_test_c)
        mse_c = mean_squared_error(y_test_c, y_pred_c)
        r2_c = r2_score(y_test_c, y_pred_c)
        print(f"Charging Model - Mean Squared Error: {mse_c:.2f}")
        print(f"Charging Model - R-squared: {r2_c:.2f}")
    else:
        print("No valid charging data available.")

    # Discharging model (time to full discharge)
    if not discharging_df.empty:
        X_discharge = discharging_df[features]
        y_discharge = discharging_df['time_to_zero']
        X_train_d, X_test_d, y_train_d, y_test_d = train_test_split(X_discharge, y_discharge, test_size=0.2, random_state=42)
        discharge_model = XGBRegressor(n_estimators=100, learning_rate=0.05, max_depth=5, reg_lambda=0.5, reg_alpha=0.05, random_state=42)
        discharge_model.fit(X_train_d, y_train_d)
        y_pred_d = discharge_model.predict(X_test_d)
        mse_d = mean_squared_error(y_test_d, y_pred_d)
        r2_d = r2_score(y_test_d, y_pred_d)
        print(f"Discharging Model - Mean Squared Error: {mse_d:.2f}")
        print(f"Discharging Model - R-squared: {r2_d:.2f}")
    else:
        print("No valid discharging data available.")

    # Step 7: Preprocess the latest data for prediction
    latest_df['timestamp'] = pd.to_datetime(latest_df['timestamp'])
    latest_df['soc'] = latest_df['soc'].clip(upper=100.0)
    latest_df['soc_distance_to_full'] = 100.0 - latest_df['soc']
    latest_df['soc_distance_to_zero'] = latest_df['soc']
    latest_df['time_diff'] = 1.0
    latest_df['voltage_slope'] = 0.0
    latest_df['time_since_state_change'] = 0.0

    # Theoretical soc_slope based on 500 mA, 2000 mAh battery
    theoretical_soc_slope = (500 / 2000) * (100 / 3600)  # 0.006944 %/s for charging
    if not charging_df.empty and latest_df['current'].iloc[0] > 0:
        avg_soc_slope = charging_df['soc_slope'].mean()
        latest_df['soc_slope'] = avg_soc_slope if not pd.isna(avg_soc_slope) and avg_soc_slope != 0 else theoretical_soc_slope
    elif not discharging_df.empty and latest_df['current'].iloc[0] < 0:
        avg_soc_slope = discharging_df['soc_slope'].mean()
        latest_df['soc_slope'] = avg_soc_slope if not pd.isna(avg_soc_slope) and avg_soc_slope != 0 else -theoretical_soc_slope

    # Step 8: Detect charging completion and predict based on current state
    charging_complete = latest_df['soc'].iloc[0] >= 98.0 and abs(latest_df['soc_slope'].iloc[0]) < 0.001  # SOC near 100% and slope near 0
    latest_X = latest_df[features]
    if charging_complete:
        prediction_type = "Charging Complete"
        latest_df['predicted_time'] = 0
    elif latest_df['current'].iloc[0] > 0 and not charging_df.empty:
        latest_df['predicted_time'] = charge_model.predict(latest_X)
        prediction_type = "Time to Full Charge"
    elif latest_df['current'].iloc[0] < 0 and not discharging_df.empty:
        latest_df['predicted_time'] = discharge_model.predict(latest_X)
        prediction_type = "Time to Full Discharge"
    else:
        prediction_type = "No prediction available (insufficient data)"
        latest_df['predicted_time'] = np.nan

    # Step 9: Display the latest prediction
    print(f"\nLatest Prediction ({prediction_type} in Hours and Minutes):")
    latest_df_display = latest_df[['timestamp', 'voltage', 'soc', 'temperature', 'current']].copy()
    latest_df_display['predicted_time'] = latest_df['predicted_time'].apply(seconds_to_hm)
    print(latest_df_display)

    # Wait for the next update
    time.sleep(update_interval)
import numpy as np
import pandas as pd

# Dataset ya limpio
df = pd.read_csv("dataset_limpio.csv", parse_dates=["ts_ec"])

# Publicaciones por cuenta
user_activity = df.groupby("handle")["ts_ec"].count().reset_index(name="tweets")
user_activity["tweets_per_day"] = user_activity["tweets"] / (
    (df["ts_ec"].max() - df["ts_ec"].min()).days + 1
)

# Posibles bots: más de 50 tweets/día en ventana observada
suspects = user_activity[user_activity["tweets_per_day"] > 50]
print("Posibles cuentas automatizadas:\n", suspects.head())

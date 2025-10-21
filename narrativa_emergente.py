import pandas as pd
from collections import Counter
import matplotlib.pyplot as plt

# Dataset ya limpio
df = pd.read_csv("dataset_limpio.csv", parse_dates=["ts_ec"])

# Top hashtags por día
def extract_hashtags(text):
    return [w for w in str(text).split() if w.startswith("#")]

df["hashtags"] = df["text"].apply(extract_hashtags)

# Expandir hashtags en filas
all_tags = df.explode("hashtags").dropna(subset=["hashtags"])
trend = all_tags.groupby([all_tags["ts_ec"].dt.date,"hashtags"]).size().reset_index(name="freq")

# Detectar hashtags con mayor crecimiento (emergentes)
trend_today = trend[trend["ts_ec"] == trend["ts_ec"].max()]
print(trend_today.sort_values("freq", ascending=False).head(10))

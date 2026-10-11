"""Base class for trading strategies.

A strategy owns its entry signals, its indicator computation and its exit
model. The backtester and the live bot depend only on this interface, so a new
strategy can be added and switched to via the registry in ``strategies/__init__``
and the ``STRATEGY`` config value.

Interface:

    * ``analyze(df_h1, df_m15, funding_rate)`` -> signal dict | None   (scalar, live)
    * ``prepare(h1, m15, funding)`` -> prepared DataFrame              (vectorised, backtest)
    * ``signals(df)`` -> (long_mask, short_mask)                       (vectorised entries)
    * ``diagnose(df)`` -> condition funnel                             (diagnostics)
    * ``entry_spec(df, i, side, price)`` -> {"sl": ..., "tp": ...}     (per-entry stops/targets)
    * ``position_manager(position, risk)`` -> exit model object        (with on_bar(low, high, close))
"""

from __future__ import annotations

import itertools

import pandas as pd

H1_MS = 3600 * 1000


def grid_product(**dims):
    """Cartesian product over parameter dimensions, yielding one dict each."""
    keys = list(dims.keys())
    for values in itertools.product(*dims.values()):
        yield dict(zip(keys, values))


class StrategyBase:
    name = "base"
    description = ""
    # Params that affect the output of ``prepare`` (indicator/level computation).
    # The sweep caches prepared data per combination of these params.
    PREPARE_PARAMS: tuple = ()

    @staticmethod
    def default_params() -> dict:
        raise NotImplementedError

    @staticmethod
    def sweep_grid() -> dict:
        return {}

    def __init__(self, params: dict | None = None):
        self.params = {**self.default_params(), **(params or {})}

    # ------------------------------------------------------------------ #
    # Scalar (live) entry
    # ------------------------------------------------------------------ #
    def analyze(self, df_h1: pd.DataFrame, df_m15: pd.DataFrame, funding_rate: float) -> dict | None:
        raise NotImplementedError

    # ------------------------------------------------------------------ #
    # Vectorised (backtest) pipeline
    # ------------------------------------------------------------------ #
    def prepare(self, h1: pd.DataFrame, m15: pd.DataFrame, funding: pd.DataFrame | None = None) -> pd.DataFrame:
        raise NotImplementedError

    def signals(self, df: pd.DataFrame) -> tuple:
        raise NotImplementedError

    def diagnose(self, df: pd.DataFrame) -> dict:
        raise NotImplementedError

    # ------------------------------------------------------------------ #
    # Entry / exit model
    # ------------------------------------------------------------------ #
    def entry_spec(self, df: pd.DataFrame, i: int, side: str, price: float) -> dict:
        raise NotImplementedError

    def position_manager(self, position: dict, risk):
        raise NotImplementedError

    # ------------------------------------------------------------------ #
    # Shared alignment helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _align_h1(m15: pd.DataFrame, h1: pd.DataFrame, columns: list) -> pd.DataFrame:
        """Merge H1 columns onto the M15 timeline using the H1 bar's *close* time."""
        h1 = h1.copy()
        h1["_close_time"] = h1["timestamp"] + H1_MS
        ref = h1[["_close_time"] + columns].sort_values("_close_time")
        merged = pd.merge_asof(
            m15.sort_values("timestamp"),
            ref,
            left_on="timestamp",
            right_on="_close_time",
            direction="backward",
        )
        return merged.drop(columns=["_close_time"]).reset_index(drop=True)

    @staticmethod
    def _align_funding(df: pd.DataFrame, funding: pd.DataFrame | None) -> pd.DataFrame:
        if funding is not None and not funding.empty:
            f = funding[["timestamp", "funding_rate"]].sort_values("timestamp")
            df = pd.merge_asof(df, f, on="timestamp", direction="backward")
            df["funding_rate"] = df["funding_rate"].fillna(0.0)
        else:
            df["funding_rate"] = 0.0
        return df

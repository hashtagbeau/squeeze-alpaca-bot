"""Checklist jsonl and the squeeze-trader CSV. mode is always paper."""

import csv
import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from squeeze_bot.constants import CSV_COLUMNS, CSV_HEADER
from squeeze_bot.models import CheckItem, Decision
from squeeze_bot.money import format_num, to_mt_iso


class LogBook:
    def __init__(self, checklist_path: Path, log_csv: Path) -> None:
        self.checklist_path = checklist_path
        self.log_csv = log_csv

    def write_checklist(
        self,
        items: list[CheckItem],
        *,
        symbol: str,
        when: datetime,
    ) -> None:
        self.checklist_path.parent.mkdir(parents=True, exist_ok=True)
        stamp = to_mt_iso(when)
        with self.checklist_path.open("a", encoding="utf-8") as handle:
            for item in items:
                record = {
                    "timestamp": stamp,
                    "ticker": symbol,
                    "item": item.item,
                    "result": item.result,
                    "source": item.source,
                    "detail": item.detail,
                }
                handle.write(json.dumps(record, separators=(",", ":")) + "\n")

    def write_row(
        self,
        *,
        when: datetime,
        ticker: str,
        row_type: str,
        grade: str,
        price: object = None,
        pct_change: object = None,
        rel_vol: object = None,
        float_shares: object = None,
        news_url: str = "",
        entry: object = None,
        stop: object = None,
        target: object = None,
        rr: object = None,
        exit_reason: str = "",
        pnl: object = None,
        reasons: str = "",
        mode: str = "paper",
    ) -> None:
        self.log_csv.parent.mkdir(parents=True, exist_ok=True)
        fresh = not self.log_csv.exists() or self.log_csv.stat().st_size == 0
        row = [
            to_mt_iso(when),
            ticker,
            row_type,
            grade,
            format_num(price),
            format_num(pct_change),
            format_num(rel_vol),
            format_num(float_shares),
            news_url,
            format_num(entry),
            format_num(stop),
            format_num(target),
            format_num(rr),
            exit_reason,
            format_num(pnl),
            mode,
            reasons,
        ]
        if len(row) != len(CSV_COLUMNS):
            raise RuntimeError("log row does not match the signed-off header")
        with self.log_csv.open("a", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            if fresh:
                writer.writerow(CSV_HEADER.split(","))
            writer.writerow(row)

    def write_no_trade(
        self,
        decision: Decision,
        when: datetime,
        extra_reasons: list[str] | None = None,
    ) -> None:
        reasons = list(decision.reasons)
        if extra_reasons:
            reasons.extend(extra_reasons)
        self.write_row(
            when=when,
            ticker=decision.symbol,
            row_type="no-trade",
            grade=decision.grade,
            price=decision.price,
            pct_change=decision.pct_change,
            rel_vol=decision.rel_vol,
            float_shares=decision.float_shares,
            news_url=decision.news_url,
            entry=decision.entry,
            stop=decision.stop,
            target=decision.target,
            rr=decision.rr,
            reasons=";".join(reasons),
        )

    def write_trade(
        self,
        *,
        when: datetime,
        symbol: str,
        grade: str,
        price: object,
        pct_change: object,
        rel_vol: object,
        float_shares: object,
        news_url: str,
        fill: Decimal | float,
        stop: object,
        target: object,
        rr: object,
        exit_reason: str,
        pnl: object,
        reasons: str = "",
    ) -> None:
        """A completed trade. entry is the actual fill; rr is the realized R multiple."""
        self.write_row(
            when=when,
            ticker=symbol,
            row_type="trade",
            grade=grade,
            price=price,
            pct_change=pct_change,
            rel_vol=rel_vol,
            float_shares=float_shares,
            news_url=news_url,
            entry=fill,
            stop=stop,
            target=target,
            rr=rr,
            exit_reason=exit_reason,
            pnl=pnl,
            reasons=reasons,
        )

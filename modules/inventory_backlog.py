import numpy as np
import pandas as pd


MIN_REMAINING_SHELF_MONTHS = 3
EPSILON = 1e-9


OUTPUT_COLUMNS = [
    "Scenario",
    "SKU",
    "SKU Alias",
    "Item Name",
    "Data Role",
    "Demand Month",
    "Due Date",
    "Demand Ton",
    "On-Time Fulfilled Ton",
    "On-Time Fulfillment (%)",
    "Late Demand Ton",
    "Total Fulfilled Ton",
    "Final Backlog Ton",
    "Inventory After Due Ton",
    "System Backlog After Due Ton",
    "Expired Inventory Until Due Ton",
    "Fully Fulfilled Date",
    "Delay Days",
    "Status",
]


def _empty_output():
    return pd.DataFrame(columns=OUTPUT_COLUMNS)


def _normalize_role(series):
    return (
        series
        .fillna("evaluation")
        .astype(str)
        .str.strip()
        .str.lower()
    )


def _usable_until(production_date, shelf_life_months):
    shelf_life_months = int(
        np.floor(
            max(
                float(shelf_life_months),
                0.0,
            )
        )
    )

    usable_months = max(
        shelf_life_months
        - MIN_REMAINING_SHELF_MONTHS,
        0,
    )

    return (
        pd.Timestamp(production_date)
        + pd.DateOffset(months=usable_months)
    ).normalize()


def build_inventory_backlog_table(
    forecast_df,
    planned_jobs_df,
    scenario_code,
    growth,
):
    """
    Membentuk ledger stok dan backlog per SKU-bulan.

    Aturan utama:
    1. Demand jatuh tempo pada MonthDueDate.
    2. Produksi pada atau sebelum due date dihitung tepat waktu.
    3. Stok digunakan dengan prinsip FEFO.
    4. Backlog tertua dipenuhi lebih dahulu.
    5. Produk hanya boleh digunakan selama masih memiliki
       minimum tiga bulan sisa shelf life.
    """
    if forecast_df is None or len(forecast_df) == 0:
        return _empty_output()

    demand_df = forecast_df.copy()

    required_demand_columns = {
        "SkuId",
        "ItemName",
        "ForecastTon",
        "DataRole",
        "Date",
        "MonthDueDate",
    }

    missing_demand_columns = (
        required_demand_columns
        - set(demand_df.columns)
    )

    if missing_demand_columns:
        raise ValueError(
            "Kolom input untuk ledger stok-backlog belum lengkap: "
            + ", ".join(
                sorted(missing_demand_columns)
            )
        )

    if "SKU_Alias" not in demand_df.columns:
        demand_df["SKU_Alias"] = ""

    demand_df["SkuId"] = (
        demand_df["SkuId"]
        .astype(str)
        .str.strip()
    )

    demand_df["DataRole"] = _normalize_role(
        demand_df["DataRole"]
    )

    demand_df["Demand Ton"] = (
        pd.to_numeric(
            demand_df["ForecastTon"],
            errors="coerce",
        )
        .fillna(0.0)
        .clip(lower=0.0)
    )

    evaluation_mask = demand_df[
        "DataRole"
    ].eq("evaluation")

    demand_df.loc[
        evaluation_mask,
        "Demand Ton",
    ] = (
        demand_df.loc[
            evaluation_mask,
            "Demand Ton",
        ]
        * (1.0 + float(growth))
    )

    demand_df["Date"] = pd.to_datetime(
        demand_df["Date"],
        errors="coerce",
    ).dt.normalize()

    demand_df["Due Date"] = pd.to_datetime(
        demand_df["MonthDueDate"],
        errors="coerce",
    ).dt.normalize()

    if demand_df["Date"].isna().any():
        raise ValueError(
            "Sebagian Date tidak dapat dibaca pada ledger stok-backlog."
        )

    if demand_df["Due Date"].isna().any():
        raise ValueError(
            "Sebagian MonthDueDate tidak dapat dibaca pada ledger stok-backlog."
        )

    demand_df = (
        demand_df[
            demand_df["Demand Ton"] > EPSILON
        ]
        .sort_values(
            by=[
                "SkuId",
                "Due Date",
                "Date",
            ],
            kind="stable",
        )
        .reset_index(drop=True)
    )

    if demand_df.empty:
        return _empty_output()

    demand_df["Ledger Row"] = np.arange(
        len(demand_df),
        dtype=int,
    )

    if planned_jobs_df is None or len(planned_jobs_df) == 0:
        production_df = pd.DataFrame(
            columns=[
                "SKU",
                "Calendar Date",
                "Batch Ton",
                "Shelf Life",
            ]
        )
    else:
        production_df = planned_jobs_df.copy()

    required_production_columns = {
        "SKU",
        "Calendar Date",
        "Batch Ton",
        "Shelf Life",
    }

    missing_production_columns = (
        required_production_columns
        - set(production_df.columns)
    )

    if missing_production_columns:
        raise ValueError(
            "Kolom Production Plan untuk ledger stok-backlog belum lengkap: "
            + ", ".join(
                sorted(missing_production_columns)
            )
        )

    if "Scenario" in production_df.columns:
        production_df = production_df[
            production_df["Scenario"]
            .astype(str)
            .eq(str(scenario_code))
        ].copy()

    production_df["SKU"] = (
        production_df["SKU"]
        .astype(str)
        .str.strip()
    )

    production_df["Production Date"] = pd.to_datetime(
        production_df["Calendar Date"],
        errors="coerce",
    ).dt.normalize()

    production_df["Batch Ton"] = (
        pd.to_numeric(
            production_df["Batch Ton"],
            errors="coerce",
        )
        .fillna(0.0)
        .clip(lower=0.0)
    )

    production_df["Shelf Life"] = (
        pd.to_numeric(
            production_df["Shelf Life"],
            errors="coerce",
        )
        .fillna(0.0)
        .clip(lower=0.0)
    )

    production_df = production_df[
        production_df["Production Date"].notna()
        & (production_df["Batch Ton"] > EPSILON)
    ].copy()

    records = []

    for sku_id, sku_demand_df in demand_df.groupby(
        "SkuId",
        sort=True,
    ):
        sku_production_df = production_df[
            production_df["SKU"].eq(
                str(sku_id)
            )
        ].copy()

        events = {}

        for _, production_row in sku_production_df.iterrows():
            production_date = pd.Timestamp(
                production_row["Production Date"]
            ).normalize()

            events.setdefault(
                production_date,
                {
                    "production": [],
                    "demand": [],
                },
            )["production"].append(
                {
                    "remaining": float(
                        production_row["Batch Ton"]
                    ),
                    "production_date": production_date,
                    "usable_until": _usable_until(
                        production_date,
                        production_row["Shelf Life"],
                    ),
                }
            )

        demand_entries = {}

        for _, demand_row in sku_demand_df.iterrows():
            due_date = pd.Timestamp(
                demand_row["Due Date"]
            ).normalize()

            entry = {
                "row": int(
                    demand_row["Ledger Row"]
                ),
                "due_date": due_date,
                "demand": float(
                    demand_row["Demand Ton"]
                ),
                "remaining": float(
                    demand_row["Demand Ton"]
                ),
                "fulfilled_total": 0.0,
                "on_time_fulfilled": 0.0,
                "fully_fulfilled_date": pd.NaT,
                "inventory_after_due": 0.0,
                "system_backlog_after_due": 0.0,
                "expired_until_due": 0.0,
                "meta": demand_row.to_dict(),
            }

            demand_entries[entry["row"]] = entry

            events.setdefault(
                due_date,
                {
                    "production": [],
                    "demand": [],
                },
            )["demand"].append(entry)

        inventory_lots = []
        backlog_entries = []
        expired_inventory_ton = 0.0

        for event_date in sorted(events):
            event_date = pd.Timestamp(
                event_date
            ).normalize()

            valid_inventory_lots = []

            for inventory_lot in inventory_lots:
                if inventory_lot["remaining"] <= EPSILON:
                    continue

                if inventory_lot["usable_until"] < event_date:
                    expired_inventory_ton += inventory_lot[
                        "remaining"
                    ]
                else:
                    valid_inventory_lots.append(
                        inventory_lot
                    )

            inventory_lots = valid_inventory_lots

            inventory_lots.extend(
                events[event_date]["production"]
            )

            inventory_lots.sort(
                key=lambda lot: (
                    lot["usable_until"],
                    lot["production_date"],
                )
            )

            backlog_entries.extend(
                events[event_date]["demand"]
            )

            backlog_entries.sort(
                key=lambda entry: (
                    entry["due_date"],
                    entry["row"],
                )
            )

            for backlog_entry in backlog_entries:
                if backlog_entry["remaining"] <= EPSILON:
                    continue

                for inventory_lot in inventory_lots:
                    if backlog_entry["remaining"] <= EPSILON:
                        break

                    if inventory_lot["remaining"] <= EPSILON:
                        continue

                    if inventory_lot["usable_until"] < event_date:
                        continue

                    used_ton = min(
                        backlog_entry["remaining"],
                        inventory_lot["remaining"],
                    )

                    backlog_entry["remaining"] -= used_ton
                    backlog_entry["fulfilled_total"] += used_ton
                    inventory_lot["remaining"] -= used_ton

                    if event_date <= backlog_entry["due_date"]:
                        backlog_entry[
                            "on_time_fulfilled"
                        ] += used_ton

                if (
                    backlog_entry["remaining"] <= EPSILON
                    and pd.isna(
                        backlog_entry[
                            "fully_fulfilled_date"
                        ]
                    )
                ):
                    backlog_entry["remaining"] = 0.0
                    backlog_entry[
                        "fully_fulfilled_date"
                    ] = event_date

            backlog_entries = [
                entry
                for entry in backlog_entries
                if entry["remaining"] > EPSILON
            ]

            inventory_after_event = sum(
                lot["remaining"]
                for lot in inventory_lots
                if lot["remaining"] > EPSILON
            )

            backlog_after_event = sum(
                entry["remaining"]
                for entry in backlog_entries
            )

            for due_entry in events[event_date]["demand"]:
                due_entry[
                    "inventory_after_due"
                ] = inventory_after_event

                due_entry[
                    "system_backlog_after_due"
                ] = backlog_after_event

                due_entry[
                    "expired_until_due"
                ] = expired_inventory_ton

        for demand_entry in demand_entries.values():
            meta = demand_entry["meta"]
            demand_ton = demand_entry["demand"]
            on_time_ton = min(
                demand_entry["on_time_fulfilled"],
                demand_ton,
            )
            final_backlog_ton = max(
                demand_entry["remaining"],
                0.0,
            )
            total_fulfilled_ton = max(
                demand_ton - final_backlog_ton,
                0.0,
            )
            late_demand_ton = max(
                demand_ton - on_time_ton,
                0.0,
            )

            fully_fulfilled_date = demand_entry[
                "fully_fulfilled_date"
            ]

            if pd.notna(fully_fulfilled_date):
                delay_days = max(
                    int(
                        (
                            fully_fulfilled_date
                            - demand_entry["due_date"]
                        ).days
                    ),
                    0,
                )
                fully_fulfilled_text = (
                    pd.Timestamp(
                        fully_fulfilled_date
                    ).strftime("%Y-%m-%d")
                )
            else:
                delay_days = np.nan
                fully_fulfilled_text = ""

            if late_demand_ton <= EPSILON:
                status = "On Time"
            elif final_backlog_ton <= EPSILON:
                status = "Fulfilled Late"
            elif total_fulfilled_ton > EPSILON:
                status = "Partially Fulfilled"
            else:
                status = "Backlog"

            records.append(
                {
                    "Scenario": str(
                        scenario_code
                    ),
                    "SKU": str(sku_id),
                    "SKU Alias": str(
                        meta.get(
                            "SKU_Alias",
                            "",
                        )
                    ),
                    "Item Name": str(
                        meta.get(
                            "ItemName",
                            "",
                        )
                    ),
                    "Data Role": str(
                        meta.get(
                            "DataRole",
                            "evaluation",
                        )
                    ),
                    "Demand Month": pd.Timestamp(
                        meta["Date"]
                    ).strftime("%Y-%m"),
                    "Due Date": demand_entry[
                        "due_date"
                    ].strftime("%Y-%m-%d"),
                    "Demand Ton": demand_ton,
                    "On-Time Fulfilled Ton": on_time_ton,
                    "On-Time Fulfillment (%)": (
                        on_time_ton
                        / demand_ton
                        * 100.0
                        if demand_ton > EPSILON
                        else 100.0
                    ),
                    "Late Demand Ton": late_demand_ton,
                    "Total Fulfilled Ton": total_fulfilled_ton,
                    "Final Backlog Ton": final_backlog_ton,
                    "Inventory After Due Ton": demand_entry[
                        "inventory_after_due"
                    ],
                    "System Backlog After Due Ton": demand_entry[
                        "system_backlog_after_due"
                    ],
                    "Expired Inventory Until Due Ton": demand_entry[
                        "expired_until_due"
                    ],
                    "Fully Fulfilled Date": fully_fulfilled_text,
                    "Delay Days": delay_days,
                    "Status": status,
                }
            )

    output_df = pd.DataFrame(
        records,
        columns=OUTPUT_COLUMNS,
    )

    numeric_columns = [
        "Demand Ton",
        "On-Time Fulfilled Ton",
        "On-Time Fulfillment (%)",
        "Late Demand Ton",
        "Total Fulfilled Ton",
        "Final Backlog Ton",
        "Inventory After Due Ton",
        "System Backlog After Due Ton",
        "Expired Inventory Until Due Ton",
        "Delay Days",
    ]

    for column in numeric_columns:
        output_df[column] = pd.to_numeric(
            output_df[column],
            errors="coerce",
        ).round(4)

    return (
        output_df.sort_values(
            by=[
                "Scenario",
                "SKU",
                "Due Date",
            ],
            kind="stable",
        )
        .reset_index(drop=True)
    )

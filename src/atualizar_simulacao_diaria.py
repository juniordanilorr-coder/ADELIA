from __future__ import annotations

import hashlib
import math
import os
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_DIR / "DATA"
OUTPUT_DIR = PROJECT_DIR / "DATA_SIMULADA_2_SEMANAS"
LEAD_TIME_WEEKS = 2
LEAD_TIME_DAYS = LEAD_TIME_WEEKS * 7
HISTORY_DAYS = 56
RANDOM_SEED = 42

PRODUCTS_FILE = DATA_DIR / "produtos.csv"
SALES_FILE = DATA_DIR / "vendas.csv"
SALE_ITEMS_FILE = DATA_DIR / "itens_venda.csv"
STOCK_FILE = DATA_DIR / "estoque.csv"

SALES_OUT = OUTPUT_DIR / "vendas_diarias_simuladas.csv"
ORDERS_OUT = OUTPUT_DIR / "pedidos_simulados.csv"
MOVEMENTS_OUT = OUTPUT_DIR / "movimentos_estoque_simulados.csv"
STATE_OUT = OUTPUT_DIR / "estado_estoque_simulado.csv"
SUMMARY_OUT = OUTPUT_DIR / "resumo_diario_simulado.csv"
RUNS_OUT = OUTPUT_DIR / "historico_execucoes_simulacao.csv"
LOG_OUT = OUTPUT_DIR / "atualizacao_automatica.log"

SALES_COLUMNS = [
    "data",
    "produto_id",
    "demanda_solicitada_simulada",
    "unidades_vendidas_simuladas",
    "unidades_nao_atendidas_simuladas",
    "estoque_abertura",
    "estoque_fechamento",
    "status_venda_simulado",
    "origem_dado",
]
ORDER_COLUMNS = [
    "pedido_simulado_id",
    "produto_id",
    "data_pedido_simulada",
    "prazo_fornecedor_semanas_simulado",
    "data_recebimento_prevista",
    "data_recebimento_simulada",
    "quantidade_pedido_simulada",
    "status_pedido_simulado",
    "origem_dado",
]
MOVEMENT_COLUMNS = [
    "data",
    "produto_id",
    "tipo_movimento",
    "quantidade",
    "estoque_apos_movimento",
    "origem_dado",
]
STATE_COLUMNS = [
    "produto_id",
    "nome_produto",
    "categoria",
    "quantidade_disponivel_simulada",
    "estoque_minimo",
    "status_venda_simulado",
    "data_ultima_atualizacao",
    "prazo_fornecedor_semanas_simulado",
    "origem_dado",
]
SUMMARY_COLUMNS = [
    "data",
    "demanda_solicitada_simulada",
    "unidades_vendidas_simuladas",
    "unidades_nao_atendidas_simuladas",
    "produtos_pausados_no_fechamento",
    "pedidos_abertos_no_fechamento",
    "origem_dado",
]
RUN_COLUMNS = [
    "executado_em",
    "data_inicial_processada",
    "data_final_processada",
    "dias_processados",
    "demanda_solicitada_simulada",
    "unidades_vendidas_simuladas",
    "unidades_nao_atendidas_simuladas",
    "status_execucao",
    "origem_dado",
]


def log(message: str) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with LOG_OUT.open("a", encoding="utf-8") as handle:
        handle.write(f"{datetime.now().isoformat(timespec='seconds')} | {message}\n")


def atomic_csv(dataframe: pd.DataFrame, path: Path) -> None:
    temporary_path = path.with_name(path.name + ".tmp")
    dataframe.to_csv(temporary_path, index=False, encoding="utf-8-sig")
    os.replace(temporary_path, path)


def read_existing(path: Path, columns: list[str], date_columns: list[str] | None = None) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=columns)
    dataframe = pd.read_csv(path, encoding="utf-8-sig")
    for column in date_columns or []:
        if column in dataframe.columns:
            dataframe[column] = pd.to_datetime(dataframe[column], errors="coerce").dt.normalize()
    return dataframe


def sample_seed(product_id: str, simulated_date: pd.Timestamp) -> int:
    seed_text = f"{RANDOM_SEED}|{product_id}|{simulated_date.date().isoformat()}"
    digest = hashlib.sha256(seed_text.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=False)


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    required_files = [PRODUCTS_FILE, SALES_FILE, SALE_ITEMS_FILE, STOCK_FILE]
    missing_files = [str(path) for path in required_files if not path.exists()]
    if missing_files:
        raise FileNotFoundError("Arquivos de entrada ausentes: " + ", ".join(missing_files))

    products = pd.read_csv(PRODUCTS_FILE, encoding="utf-8-sig")
    sales = pd.read_csv(SALES_FILE, encoding="utf-8-sig")
    sale_items = pd.read_csv(SALE_ITEMS_FILE, encoding="utf-8-sig")
    stock = pd.read_csv(STOCK_FILE, encoding="utf-8-sig")

    # Normalize IDs before joins so numeric-looking IDs remain consistent across CSVs.
    products["produto_id"] = products["produto_id"].astype(str)
    sales["venda_id"] = sales["venda_id"].astype(str)
    sale_items["venda_id"] = sale_items["venda_id"].astype(str)
    sale_items["produto_id"] = sale_items["produto_id"].astype(str)
    stock["produto_id"] = stock["produto_id"].astype(str)
    if set(stock["produto_id"]) != set(products["produto_id"]):
        raise ValueError("Os CSVs de produtos e estoque devem conter os mesmos produto_id.")

    sales["data_venda"] = pd.to_datetime(sales["data_venda"], errors="raise").dt.normalize()
    stock["data_referencia"] = pd.to_datetime(stock["data_referencia"], errors="raise").dt.normalize()
    reference_date = stock["data_referencia"].max()
    today = pd.Timestamp.today().normalize()
    simulation_end = today

    if stock["produto_id"].duplicated().any():
        raise ValueError("A tabela de estoque deve ter um único registro por produto.")
    if products["produto_id"].duplicated().any():
        raise ValueError("A tabela de produtos deve ter um único registro por produto.")

    product_ids = sorted(products["produto_id"].astype(str).unique())
    product_names = products.set_index("produto_id")["nome_produto"].to_dict()
    categories = products.set_index("produto_id")["categoria"].to_dict()
    stock_by_product = stock.set_index("produto_id")["quantidade_disponivel"].astype(int).to_dict()
    minimum_by_product = stock.set_index("produto_id")["estoque_minimo"].astype(int).to_dict()

    history_start = reference_date - pd.Timedelta(days=HISTORY_DAYS - 1)
    historical_items = sale_items.merge(
        sales[["venda_id", "data_venda"]],
        on="venda_id",
        how="left",
        validate="many_to_one",
    )
    historical_items = historical_items[
        historical_items["data_venda"].between(history_start, reference_date)
    ]
    observed_daily = (
        historical_items.groupby(["produto_id", "data_venda"], as_index=False)
        .agg(demand=("quantidade", "sum"))
        .rename(columns={"data_venda": "data"})
    )
    history_dates = pd.date_range(history_start, reference_date, freq="D")
    history_grid = pd.MultiIndex.from_product(
        [product_ids, history_dates], names=["produto_id", "data"]
    ).to_frame(index=False)
    history_grid = history_grid.merge(
        observed_daily, on=["produto_id", "data"], how="left", validate="one_to_one"
    )
    history_grid["demand"] = history_grid["demand"].fillna(0).astype(int)
    history_grid["weekday"] = history_grid["data"].dt.dayofweek

    distributions: dict[tuple[str, int], np.ndarray] = {}
    average_daily: dict[str, float] = {}
    for product_id in product_ids:
        product_history = history_grid[history_grid["produto_id"] == product_id]
        average_daily[product_id] = float(product_history["demand"].mean())
        for weekday in range(7):
            values = product_history.loc[product_history["weekday"] == weekday, "demand"].to_numpy()
            distributions[(product_id, weekday)] = values if len(values) else np.array([0], dtype=int)

    # Match the notebook's one seeded demand sequence across all simulated dates.
    sampled_demand: dict[tuple[str, pd.Timestamp], int] = {}
    demand_rng = np.random.default_rng(RANDOM_SEED)
    all_simulation_dates = pd.date_range(
        start=reference_date + pd.Timedelta(days=1), end=simulation_end, freq="D"
    )
    for simulated_date in all_simulation_dates:
        for product_id in product_ids:
            sample = distributions[(product_id, simulated_date.dayofweek)]
            sampled_demand[(product_id, simulated_date)] = int(demand_rng.choice(sample))

    state_exists = STATE_OUT.exists()
    if state_exists:
        state_df = pd.read_csv(STATE_OUT, encoding="utf-8-sig")
        state_df["produto_id"] = state_df["produto_id"].astype(str)
        state_df["data_ultima_atualizacao"] = pd.to_datetime(
            state_df["data_ultima_atualizacao"], errors="raise"
        ).dt.normalize()
        if set(state_df["produto_id"]) != set(product_ids):
            raise ValueError("Os produtos do estado simulado não correspondem aos CSVs-base.")
        stock_by_product = state_df.set_index("produto_id")["quantidade_disponivel_simulada"].astype(int).to_dict()
        status_by_product = state_df.set_index("produto_id")["status_venda_simulado"].to_dict()
        last_processed = state_df["data_ultima_atualizacao"].max()
    else:
        status_by_product = {
            product_id: ("Pausado" if stock_by_product[product_id] <= 0 else "Ativo")
            for product_id in product_ids
        }
        last_processed = reference_date

    daily_sales_df = read_existing(SALES_OUT, SALES_COLUMNS, ["data"])
    orders_df = read_existing(
        ORDERS_OUT,
        ORDER_COLUMNS,
        ["data_pedido_simulada", "data_recebimento_prevista", "data_recebimento_simulada"],
    )
    movements_df = read_existing(MOVEMENTS_OUT, MOVEMENT_COLUMNS, ["data"])
    summaries_df = read_existing(SUMMARY_OUT, SUMMARY_COLUMNS, ["data"])
    runs_df = read_existing(RUNS_OUT, RUN_COLUMNS, ["executado_em", "data_inicial_processada", "data_final_processada"])

    orders = orders_df.to_dict("records")
    new_sales: list[dict] = []
    new_movements: list[dict] = []
    new_summaries: list[dict] = []
    order_counts = {product_id: 0 for product_id in product_ids}
    for order in orders:
        product_id = str(order["produto_id"])
        if product_id in order_counts:
            order_counts[product_id] += 1

    def open_quantity(product_id: str) -> int:
        return sum(
            int(order["quantidade_pedido_simulada"])
            for order in orders
            if str(order["produto_id"]) == product_id
            and order["status_pedido_simulado"] == "Em aberto"
        )

    def place_order(product_id: str, order_date: pd.Timestamp) -> None:
        inventory_position = stock_by_product[product_id] + open_quantity(product_id)
        target_stock = (
            minimum_by_product[product_id]
            + average_daily[product_id] * LEAD_TIME_DAYS
        )
        quantity = int(math.ceil(max(0, target_stock - inventory_position)))
        if quantity <= 0:
            return
        order_counts[product_id] += 1
        orders.append({
            "pedido_simulado_id": f"SIM2S-{product_id}-{order_date:%Y%m%d}-{order_counts[product_id]:03d}",
            "produto_id": product_id,
            "data_pedido_simulada": order_date,
            "prazo_fornecedor_semanas_simulado": LEAD_TIME_WEEKS,
            "data_recebimento_prevista": order_date + pd.Timedelta(days=LEAD_TIME_DAYS),
            "data_recebimento_simulada": pd.NaT,
            "quantidade_pedido_simulada": quantity,
            "status_pedido_simulado": "Em aberto",
            "origem_dado": "Simulado",
        })

    # Na primeira execução, criar os pedidos iniciais do cenário de 2 semanas.
    if not state_exists:
        for product_id in product_ids:
            place_order(product_id, reference_date)

    dates_to_process = pd.date_range(
        start=last_processed + pd.Timedelta(days=1),
        end=simulation_end,
        freq="D",
    )

    if len(dates_to_process) == 0:
        log(f"Sem novos dias. Último dia processado: {last_processed.date()}.")
        return

    for simulated_date in dates_to_process:
        # Recebimentos são registrados no início do dia.
        for order in orders:
            if (
                order["status_pedido_simulado"] == "Em aberto"
                and pd.Timestamp(order["data_recebimento_prevista"]).normalize() <= simulated_date
            ):
                product_id = str(order["produto_id"])
                stock_by_product[product_id] += int(order["quantidade_pedido_simulada"])
                order["status_pedido_simulado"] = "Recebido"
                order["data_recebimento_simulada"] = simulated_date
                new_movements.append({
                    "data": simulated_date,
                    "produto_id": product_id,
                    "tipo_movimento": "Recebimento simulado",
                    "quantidade": int(order["quantidade_pedido_simulada"]),
                    "estoque_apos_movimento": stock_by_product[product_id],
                    "origem_dado": "Simulado",
                })
                if (
                    status_by_product[product_id] == "Pausado"
                    and stock_by_product[product_id] >= minimum_by_product[product_id]
                ):
                    status_by_product[product_id] = "Ativo"

        for product_id in product_ids:
            requested = sampled_demand[(product_id, simulated_date)]
            opening_stock = stock_by_product[product_id]

            if status_by_product[product_id] == "Pausado" or opening_stock <= 0:
                sold = 0
                status_by_product[product_id] = "Pausado"
            else:
                sold = min(requested, opening_stock)

            unfulfilled = requested - sold
            stock_by_product[product_id] -= sold

            if stock_by_product[product_id] <= 0:
                stock_by_product[product_id] = 0
                status_by_product[product_id] = "Pausado"
            elif status_by_product[product_id] != "Pausado":
                status_by_product[product_id] = (
                    "Ativo com alerta"
                    if stock_by_product[product_id] < minimum_by_product[product_id]
                    else "Ativo"
                )

            new_sales.append({
                "data": simulated_date,
                "produto_id": product_id,
                "demanda_solicitada_simulada": requested,
                "unidades_vendidas_simuladas": sold,
                "unidades_nao_atendidas_simuladas": unfulfilled,
                "estoque_abertura": opening_stock,
                "estoque_fechamento": stock_by_product[product_id],
                "status_venda_simulado": status_by_product[product_id],
                "origem_dado": "Simulado",
            })

            if sold > 0:
                new_movements.append({
                    "data": simulated_date,
                    "produto_id": product_id,
                    "tipo_movimento": "Venda simulada",
                    "quantidade": -sold,
                    "estoque_apos_movimento": stock_by_product[product_id],
                    "origem_dado": "Simulado",
                })

            # Uma compra em aberto por SKU; nova compra quando a posição atingir o alvo.
            if open_quantity(product_id) == 0:
                place_order(product_id, simulated_date)

        todays_rows = [row for row in new_sales if row["data"] == simulated_date]
        open_orders_today = sum(
            order["status_pedido_simulado"] == "Em aberto" for order in orders
        )
        new_summaries.append({
            "data": simulated_date,
            "demanda_solicitada_simulada": sum(r["demanda_solicitada_simulada"] for r in todays_rows),
            "unidades_vendidas_simuladas": sum(r["unidades_vendidas_simuladas"] for r in todays_rows),
            "unidades_nao_atendidas_simuladas": sum(r["unidades_nao_atendidas_simuladas"] for r in todays_rows),
            "produtos_pausados_no_fechamento": sum(
                r["status_venda_simulado"] == "Pausado" for r in todays_rows
            ),
            "pedidos_abertos_no_fechamento": open_orders_today,
            "origem_dado": "Simulado",
        })

    sales_all = pd.concat([daily_sales_df, pd.DataFrame(new_sales, columns=SALES_COLUMNS)], ignore_index=True)
    orders_all = pd.DataFrame(orders, columns=ORDER_COLUMNS)
    movements_all = pd.concat([movements_df, pd.DataFrame(new_movements, columns=MOVEMENT_COLUMNS)], ignore_index=True)
    summaries_all = pd.concat([summaries_df, pd.DataFrame(new_summaries, columns=SUMMARY_COLUMNS)], ignore_index=True)

    state_rows = []
    for product_id in product_ids:
        state_rows.append({
            "produto_id": product_id,
            "nome_produto": product_names.get(product_id, ""),
            "categoria": categories.get(product_id, ""),
            "quantidade_disponivel_simulada": stock_by_product[product_id],
            "estoque_minimo": minimum_by_product[product_id],
            "status_venda_simulado": status_by_product[product_id],
            "data_ultima_atualizacao": simulation_end,
            "prazo_fornecedor_semanas_simulado": LEAD_TIME_WEEKS,
            "origem_dado": "Simulado",
        })
    state_all = pd.DataFrame(state_rows, columns=STATE_COLUMNS)

    run_row = {
        "executado_em": pd.Timestamp.now(),
        "data_inicial_processada": dates_to_process.min(),
        "data_final_processada": dates_to_process.max(),
        "dias_processados": len(dates_to_process),
        "demanda_solicitada_simulada": sum(r["demanda_solicitada_simulada"] for r in new_sales),
        "unidades_vendidas_simuladas": sum(r["unidades_vendidas_simuladas"] for r in new_sales),
        "unidades_nao_atendidas_simuladas": sum(r["unidades_nao_atendidas_simuladas"] for r in new_sales),
        "status_execucao": "Concluída",
        "origem_dado": "Simulado",
    }
    runs_all = pd.concat([runs_df, pd.DataFrame([run_row], columns=RUN_COLUMNS)], ignore_index=True)

    atomic_csv(sales_all, SALES_OUT)
    atomic_csv(orders_all, ORDERS_OUT)
    atomic_csv(movements_all, MOVEMENTS_OUT)
    atomic_csv(summaries_all, SUMMARY_OUT)
    atomic_csv(state_all, STATE_OUT)
    atomic_csv(runs_all, RUNS_OUT)

    log(
        f"Atualização concluída: {dates_to_process.min().date()} a {dates_to_process.max().date()}; "
        f"dias={len(dates_to_process)}; demanda={run_row['demanda_solicitada_simulada']}; "
        f"vendidas={run_row['unidades_vendidas_simuladas']}; "
        f"não atendidas={run_row['unidades_nao_atendidas_simuladas']}."
    )
    print(f"Atualização simulada concluída até {simulation_end.date()}.")
    print(f"Arquivos separados em: {OUTPUT_DIR}")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        log(f"FALHA: {type(error).__name__}: {error}")
        raise

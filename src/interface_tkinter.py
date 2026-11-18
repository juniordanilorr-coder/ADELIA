from __future__ import annotations

import json
import math
import tkinter as tk
from html.parser import HTMLParser
from pathlib import Path
from tkinter import messagebox, ttk

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "DATA"
SIM_DIR = ROOT / "DATA_SIMULADA_2_SEMANAS"
NOTEBOOK_FILE = ROOT / "NOTEBOOKS" / "02_modelagem_de_previsão.ipynb"
LOGO_FILE = ROOT / "assets" / "adelia_logo.png"

C = {
    "background": "#0D1012",
    "sidebar": "#111517",
    "surface": "#171C1F",
    "surface_alt": "#1D2427",
    "border": "#2B3336",
    "orange": "#F36A21",
    "orange_light": "#FF9A45",
    "orange_dark": "#783819",
    "text": "#F7F4F1",
    "muted": "#A5A9AA",
    "green": "#58C99A",
    "yellow": "#F2B95E",
    "red": "#F16A5B",
}
FONT = "Segoe UI"


def read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path, encoding="utf-8-sig")


def pt_number(value, default=0.0) -> float:
    if value is None or str(value).strip() in ("", "nan", "None"):
        return default
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace("%", "").replace(" ", "")
    if "," in text:
        text = text.replace(".", "").replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return default


def fmt_number(value, decimals=2) -> str:
    if value is None or pd.isna(value):
        return "—"
    return f"{float(value):,.{decimals}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def fmt_currency(value) -> str:
    return "R$ " + fmt_number(value, 0)


def fmt_date(value) -> str:
    if value is None or pd.isna(value):
        return "—"
    return pd.Timestamp(value).strftime("%d/%m/%Y")


class NotebookTableParser(HTMLParser):
    """Parser pequeno para tabelas DataFrame salvas dentro do notebook."""

    def __init__(self):
        super().__init__()
        self.tables = []
        self.depth = 0
        self.current_table = None
        self.current_row = None
        self.current_cell = None

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            if self.depth == 0:
                self.current_table = []
                self.tables.append(self.current_table)
            self.depth += 1
        elif self.depth == 1 and tag == "tr":
            self.current_row = []
        elif self.depth == 1 and tag in ("td", "th"):
            self.current_cell = []
        elif self.depth == 1 and tag == "br" and self.current_cell is not None:
            self.current_cell.append(" ")

    def handle_data(self, data):
        if self.depth == 1 and self.current_cell is not None:
            self.current_cell.append(data)

    def handle_endtag(self, tag):
        if self.depth == 1 and tag in ("td", "th") and self.current_cell is not None:
            value = " ".join("".join(self.current_cell).replace("\xa0", " ").split())
            self.current_row.append(value)
            self.current_cell = None
        elif self.depth == 1 and tag == "tr" and self.current_row is not None:
            if self.current_row:
                self.current_table.append(self.current_row)
            self.current_row = None
        elif tag == "table" and self.depth:
            self.depth -= 1


def notebook_tables():
    if not NOTEBOOK_FILE.exists():
        return []
    try:
        with NOTEBOOK_FILE.open("r", encoding="utf-8") as handle:
            notebook = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return []
    found = []
    for cell in notebook.get("cells", []):
        for output in cell.get("outputs", []):
            html = output.get("data", {}).get("text/html")
            if not html:
                continue
            parser = NotebookTableParser()
            parser.feed("".join(html))
            found.extend(parser.tables)
    return found


def load_model_diagnostics():
    diagnostics = []
    winners = {}
    for table in notebook_tables():
        if not table:
            continue
        header = table[0]
        records = [dict(zip(header, row)) for row in table[1:] if len(row) == len(header)]
        needed = {"produto_id", "horizonte", "MAE_Random_Forest_universal", "MAE_Selecao_individual"}
        if needed.issubset(header):
            diagnostics.extend(record for record in records if record.get("produto_id"))
        if {"estrategia", "produtos_horizonte"}.issubset(header):
            for record in records:
                strategy = str(record.get("estrategia", ""))
                if strategy:
                    winners[strategy] = int(pt_number(record.get("produtos_horizonte")))
    unique = {}
    for row in diagnostics:
        unique[(row.get("produto_id"), row.get("horizonte"))] = row
    return list(unique.values()), winners


class AppData:
    def reload(self):
        products = read_csv(DATA_DIR / "produtos.csv")
        base_stock = read_csv(DATA_DIR / "estoque.csv")
        sales = read_csv(DATA_DIR / "vendas.csv")
        items = read_csv(DATA_DIR / "itens_venda.csv")
        state = read_csv(SIM_DIR / "estado_estoque_simulado.csv")
        orders = read_csv(SIM_DIR / "pedidos_simulados.csv")
        daily = read_csv(SIM_DIR / "resumo_diario_simulado.csv")

        if products.empty or base_stock.empty:
            raise FileNotFoundError("Não encontrei produtos.csv e estoque.csv dentro da pasta DATA.")

        for frame in (products, base_stock, state, orders, items):
            if not frame.empty and "produto_id" in frame.columns:
                frame["produto_id"] = frame["produto_id"].astype(str)
        if not sales.empty:
            sales["venda_id"] = sales["venda_id"].astype(str)
            sales["data_venda"] = pd.to_datetime(sales["data_venda"], errors="coerce")
        if not items.empty:
            items["venda_id"] = items["venda_id"].astype(str)

        base_stock["data_referencia"] = pd.to_datetime(base_stock["data_referencia"], errors="coerce")
        self.reference_date = base_stock["data_referencia"].max()
        base_stock = base_stock.rename(columns={"quantidade_disponivel": "quantidade_base"})
        inventory = products.merge(base_stock, on="produto_id", how="left")

        if not state.empty:
            state["data_ultima_atualizacao"] = pd.to_datetime(state["data_ultima_atualizacao"], errors="coerce")
            inventory = inventory.merge(
                state[["produto_id", "quantidade_disponivel_simulada", "status_venda_simulado", "data_ultima_atualizacao"]],
                on="produto_id", how="left"
            )
        else:
            inventory["quantidade_disponivel_simulada"] = pd.NA
            inventory["status_venda_simulado"] = pd.NA
            inventory["data_ultima_atualizacao"] = pd.NaT

        inventory["quantidade_atual"] = pd.to_numeric(
            inventory["quantidade_disponivel_simulada"], errors="coerce"
        ).fillna(pd.to_numeric(inventory["quantidade_base"], errors="coerce")).fillna(0).astype(int)
        inventory["estoque_minimo"] = pd.to_numeric(inventory["estoque_minimo"], errors="coerce").fillna(0).astype(int)
        inventory["status_atual"] = inventory["status_venda_simulado"].fillna("")
        inventory.loc[inventory["status_atual"] == "", "status_atual"] = "Ativo"
        inventory.loc[inventory["quantidade_atual"] <= 0, "status_atual"] = "Pausado"
        inventory.loc[
            (inventory["quantidade_atual"] > 0)
            & (inventory["quantidade_atual"] < inventory["estoque_minimo"])
            & (inventory["status_atual"] != "Pausado"),
            "status_atual",
        ] = "Ativo com alerta"
        self.inventory = inventory

        if not orders.empty:
            orders["quantidade_pedido_simulada"] = pd.to_numeric(orders["quantidade_pedido_simulada"], errors="coerce").fillna(0)
            orders["data_recebimento_prevista"] = pd.to_datetime(orders["data_recebimento_prevista"], errors="coerce")
            self.open_orders = orders[orders["status_pedido_simulado"] == "Em aberto"].copy()
        else:
            self.open_orders = pd.DataFrame()

        if not daily.empty:
            daily["data"] = pd.to_datetime(daily["data"], errors="coerce")
            for col in ("demanda_solicitada_simulada", "unidades_vendidas_simuladas", "unidades_nao_atendidas_simuladas", "produtos_pausados_no_fechamento", "pedidos_abertos_no_fechamento"):
                if col in daily:
                    daily[col] = pd.to_numeric(daily[col], errors="coerce").fillna(0)
            self.daily = daily.sort_values("data")
        else:
            self.daily = pd.DataFrame()

        self.last_update = (
            pd.to_datetime(state["data_ultima_atualizacao"], errors="coerce").max()
            if not state.empty else self.reference_date
        )
        self.average_weekly = {str(pid): 0.0 for pid in products["produto_id"]}
        if not sales.empty and not items.empty:
            try:
                history = items.merge(
                    sales[["venda_id", "data_venda"]],
                    on="venda_id", how="left", validate="many_to_one"
                )
                start = self.reference_date - pd.Timedelta(days=55)
                recent = history[history["data_venda"].between(start, self.reference_date)]
                totals = recent.groupby("produto_id")["quantidade"].sum()
                self.average_weekly.update({str(k): float(v) / 8 for k, v in totals.items()})
            except (KeyError, ValueError, TypeError):
                pass

        self.diagnostics, self.winners = load_model_diagnostics()

    def recommendations(self):
        pending = {}
        if not self.open_orders.empty:
            for pid, group in self.open_orders.groupby("produto_id"):
                pending[str(pid)] = {
                    "qty": int(group["quantidade_pedido_simulada"].sum()),
                    "due": group["data_recebimento_prevista"].min(),
                }
        rows = []
        rank = {"Crítico": 0, "Alta": 1, "Atenção": 2, "Normal": 3}
        for row in self.inventory.to_dict("records"):
            pid = str(row["produto_id"])
            qty = int(row["quantidade_atual"])
            minimum = int(row["estoque_minimo"])
            avg = self.average_weekly.get(pid, 0.0)
            coverage = qty / avg if avg > 0 else None
            order = pending.get(pid, {"qty": 0, "due": pd.NaT})
            status = str(row["status_atual"])
            if qty <= 0 or status == "Pausado":
                urgency, action = "Crítico", "Recomendar suspensão temporária das vendas deste produto até o saldo atingir o mínimo."
            elif qty < minimum:
                urgency, action = "Alta", "Saldo abaixo do mínimo; conferir reposição simulada e priorizar acompanhamento."
            elif coverage is not None and coverage <= 2:
                urgency, action = "Atenção", "Cobertura estimada dentro do prazo de fornecedor de 2 semanas."
            elif order["qty"]:
                urgency, action = "Atenção", "Pedido simulado em aberto; acompanhar a data prevista de recebimento."
            else:
                urgency, action = "Normal", "Estoque acima do mínimo; manter monitoramento."
            rows.append({
                "produto_id": pid, "nome_produto": row.get("nome_produto", ""),
                "status": status, "quantidade": qty, "minimo": minimum,
                "cobertura": coverage, "pedido": int(order["qty"]),
                "data_pedido": order["due"], "urgencia": urgency, "acao": action,
                "rank": rank[urgency],
            })
        return sorted(
            rows,
            key=lambda r: (r["rank"], r["cobertura"] if r["cobertura"] is not None else -1, r["produto_id"])
        )


class BasePage(tk.Frame):
    def __init__(self, app, parent):
        super().__init__(parent, bg=C["background"])
        self.app = app
        self.data = app.data

    def section_title(self, parent, title, subtitle=None):
        tk.Label(parent, text=title, bg=C["background"], fg=C["text"], font=(FONT, 15, "bold")).pack(anchor="w")
        if subtitle:
            tk.Label(parent, text=subtitle, bg=C["background"], fg=C["muted"], font=(FONT, 9)).pack(anchor="w", pady=(4, 0))

    def table(self, parent, columns, headings, widths, height=10):
        shell = tk.Frame(parent, bg=C["surface"], highlightbackground=C["border"], highlightthickness=1)
        tree = ttk.Treeview(shell, columns=columns, show="headings", height=height)
        for key, heading, width in zip(columns, headings, widths):
            tree.heading(key, text=heading)
            tree.column(key, width=width, minwidth=60, stretch=False, anchor="w")
        ybar = ttk.Scrollbar(shell, orient="vertical", command=tree.yview)
        xbar = ttk.Scrollbar(shell, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=ybar.set, xscrollcommand=xbar.set)
        tree.pack(side="left", fill="both", expand=True, padx=(8, 0), pady=(8, 0))
        ybar.pack(side="right", fill="y", padx=(0, 6), pady=(8, 0))
        xbar.pack(side="bottom", fill="x", padx=8, pady=(0, 6))
        tree.tag_configure("critical", foreground=C["red"])
        tree.tag_configure("high", foreground=C["orange_light"])
        tree.tag_configure("normal", foreground=C["muted"])
        return shell, tree

    def stat_card(self, parent, title, value, detail, accent):
        card = tk.Frame(parent, bg=C["surface"], highlightbackground=C["border"], highlightthickness=1)
        tk.Frame(card, bg=accent, width=4).pack(side="left", fill="y")
        body = tk.Frame(card, bg=C["surface"], padx=15, pady=13)
        body.pack(side="left", fill="both", expand=True)
        tk.Label(body, text=title.upper(), bg=C["surface"], fg=C["muted"], font=(FONT, 8, "bold")).pack(anchor="w")
        tk.Label(body, text=value, bg=C["surface"], fg=C["text"], font=(FONT, 23, "bold")).pack(anchor="w", pady=(3, 0))
        tk.Label(body, text=detail, bg=C["surface"], fg=C["muted"], font=(FONT, 8), wraplength=230, justify="left").pack(anchor="w", pady=(2, 0))
        return card


class DashboardPage(BasePage):
    def __init__(self, app, parent):
        super().__init__(app, parent)
        inv = self.data.inventory
        recs = self.data.recommendations()
        sem_saldo = int((inv["quantidade_atual"] <= 0).sum())
        below = int(((inv["quantidade_atual"] < inv["estoque_minimo"]) & (inv["quantidade_atual"] > 0)).sum())
        open_count = len(self.data.open_orders)
        last7 = self.data.daily.tail(7) if not self.data.daily.empty else pd.DataFrame()
        unmet = int(last7["unidades_nao_atendidas_simuladas"].sum()) if not last7.empty else 0
        potential_revenue_4w = sum(
            self.data.average_weekly.get(str(row["produto_id"]), 0.0)
            * 4
            * pt_number(row.get("preco_venda_referencia"))
            for row in inv.to_dict("records")
        )

        hero = tk.Canvas(self, height=116, bg=C["surface"], highlightthickness=0)
        hero.pack(fill="x", pady=(0, 16))
        hero.bind("<Configure>", lambda e: self.draw_hero(hero, e.width, e.height))
        self.draw_hero(hero, 980, 116)

        cards = tk.Frame(self, bg=C["background"])
        cards.pack(fill="x", pady=(0, 16))
        card_data = [
            ("Produtos", str(len(inv)), "SKUs no catálogo", C["orange"]),
            ("Produtos sem saldo", str(sem_saldo), "Suspensão temporária sugerida por item", C["red"]),
            ("Reposições pendentes", str(open_count), "Pedidos simulados ao fornecedor, aguardando recebimento", C["yellow"]),
            ("Faturamento potencial · 4 sem.", fmt_currency(potential_revenue_4w), "Média histórica × preço de referência", C["orange_light"]),
        ]
        for i, info in enumerate(card_data):
            cards.grid_columnconfigure(i, weight=1, uniform="cards")
            self.stat_card(cards, *info).grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else 8, 0))

        lower = tk.Frame(self, bg=C["background"])
        lower.pack(fill="both", expand=True)
        left = tk.Frame(lower, bg=C["surface"], highlightbackground=C["border"], highlightthickness=1)
        left.pack(side="left", fill="both", expand=True, padx=(0, 8))
        tk.Label(left, text="Movimento simulado recente", bg=C["surface"], fg=C["text"], font=(FONT, 12, "bold")).pack(anchor="w", padx=16, pady=(14, 0))
        tk.Label(left, text="Unidades vendidas e não atendidas por dia", bg=C["surface"], fg=C["muted"], font=(FONT, 9)).pack(anchor="w", padx=16, pady=(3, 0))
        chart = tk.Canvas(left, height=240, bg=C["surface"], highlightthickness=0)
        chart.pack(fill="both", expand=True, padx=10, pady=8)
        chart.bind("<Configure>", lambda e: draw_daily_chart(chart, self.data.daily.tail(14), e.width, e.height))
        draw_daily_chart(chart, self.data.daily.tail(14), 600, 240)

        right = tk.Frame(lower, width=365, bg=C["surface"], highlightbackground=C["border"], highlightthickness=1)
        right.pack(side="right", fill="y")
        right.pack_propagate(False)
        tk.Label(right, text="Prioridades de estoque", bg=C["surface"], fg=C["text"], font=(FONT, 12, "bold")).pack(anchor="w", padx=16, pady=(14, 3))
        tk.Label(right, text="Sugestões por produto · prazo de fornecedor: 2 semanas", bg=C["surface"], fg=C["muted"], font=(FONT, 8)).pack(anchor="w", padx=16, pady=(0, 8))
        scroll_shell = tk.Frame(right, bg=C["surface"])
        scroll_shell.pack(fill="both", expand=True, padx=(10, 6), pady=(0, 10))
        priority_canvas = tk.Canvas(scroll_shell, bg=C["surface"], highlightthickness=0)
        priority_scroll = ttk.Scrollbar(scroll_shell, orient="vertical", command=priority_canvas.yview)
        priority_canvas.configure(yscrollcommand=priority_scroll.set)
        priority_canvas.pack(side="left", fill="both", expand=True)
        priority_scroll.pack(side="right", fill="y")
        priority_list = tk.Frame(priority_canvas, bg=C["surface"])
        priority_window = priority_canvas.create_window((0, 0), window=priority_list, anchor="nw")
        priority_list.bind("<Configure>", lambda _e: priority_canvas.configure(scrollregion=priority_canvas.bbox("all")))
        priority_canvas.bind("<Configure>", lambda e: priority_canvas.itemconfigure(priority_window, width=e.width))
        for item in recs:
            row = tk.Frame(priority_list, bg=C["surface"], padx=8, pady=8)
            row.pack(fill="x")
            color = C["red"] if item["urgencia"] == "Crítico" else C["orange_light"] if item["urgencia"] in ("Alta", "Atenção") else C["green"]
            tk.Label(row, text="●", bg=C["surface"], fg=color, font=(FONT, 10)).pack(side="left", anchor="n", padx=(0, 7))
            text = tk.Frame(row, bg=C["surface"])
            text.pack(side="left", fill="x", expand=True)
            tk.Label(text, text=f'{item["produto_id"]}  {item["nome_produto"]}', bg=C["surface"], fg=C["text"], font=(FONT, 9, "bold"), wraplength=275, justify="left").pack(anchor="w")
            tk.Label(text, text=item["urgencia"] + " · " + item["acao"], bg=C["surface"], fg=C["muted"], font=(FONT, 8), wraplength=275, justify="left").pack(anchor="w", pady=(2, 0))
        tk.Label(self, text=f"Atualizado até {fmt_date(self.data.last_update)} · registros simulados", bg=C["background"], fg=C["muted"], font=(FONT, 8)).pack(anchor="w", pady=(10, 0))

    @staticmethod
    def draw_hero(canvas, width, height):
        canvas.delete("all")
        canvas.create_text(22, 29, anchor="w", text="Sistema Inteligente de Apoio à Decisão", fill=C["muted"], font=(FONT, 9))
        canvas.create_text(22, 57, anchor="w", text="Estoque e previsão, em uma só visão", fill=C["text"], font=(FONT, 19, "bold"))
        canvas.create_text(22, 86, anchor="w", text="Acompanhe risco, desempenho dos modelos e recomendações simuladas.", fill=C["muted"], font=(FONT, 9))
        colors = ["#5C2B16", "#733514", "#914012", "#B64C12", C["orange"]]
        for i, color in enumerate(colors):
            points = []
            for x in range(int(width * 0.52), width + 8, 8):
                t = x / max(width, 1)
                y = height * 0.66 + math.sin(t * 8 + i * 0.32) * (12 + i * 1.5) + (i - 2) * 8
                points.extend((x, y))
            canvas.create_line(*points, smooth=True, fill=color, width=1)


class ForecastPage(BasePage):
    def __init__(self, app, parent):
        super().__init__(app, parent)
        self.horizon = tk.StringVar(value="4")
        self.section_title(self, "Faturamento futuro", f"Estimativa pela média das últimas 8 semanas históricas completas, até {fmt_date(self.data.reference_date)}.")
        self.summary = tk.Frame(self, bg=C["background"])
        self.summary.pack(fill="x", pady=(16, 14))
        controls = tk.Frame(self, bg=C["background"])
        controls.pack(fill="x", pady=(0, 10))
        tk.Label(controls, text="Período", bg=C["background"], fg=C["muted"], font=(FONT, 9)).pack(side="left")
        combo = ttk.Combobox(controls, textvariable=self.horizon, values=("4", "8"), state="readonly", width=7)
        combo.pack(side="left", padx=(8, 5))
        tk.Label(controls, text="semanas", bg=C["background"], fg=C["muted"], font=(FONT, 9)).pack(side="left")
        combo.bind("<<ComboboxSelected>>", lambda _e: self.render_projection())
        tk.Button(controls, text="Entender MAE e WAPE", command=self.open_metrics, relief="flat", bd=0, bg=C["surface_alt"], fg=C["text"], activebackground=C["orange_dark"], activeforeground=C["text"], font=(FONT, 8, "bold"), padx=10, pady=7, cursor="hand2").pack(side="right")
        shell, self.tree = self.table(
            self, ("sku", "produto", "media", "unidades", "preco", "receita"),
            ("SKU", "Produto", "Média semanal", "Unidades estimadas", "Preço referência", "Faturamento potencial"),
            (75, 220, 110, 135, 140, 170), 13
        )
        shell.pack(fill="both", expand=True)
        tk.Label(self, text="Conta: média semanal vendida nas últimas 8 semanas históricas completas × período × preço de referência. É faturamento bruto potencial; não desconta falta de estoque, descontos, custos ou devoluções.", bg=C["background"], fg=C["muted"], font=(FONT, 8), wraplength=980, justify="left").pack(anchor="w", pady=(9, 0))
        self.render_projection()

    def render_projection(self):
        self.tree.delete(*self.tree.get_children())
        weeks = int(self.horizon.get())
        rows = []
        for product in self.data.inventory.to_dict("records"):
            pid = str(product["produto_id"])
            average = self.data.average_weekly.get(pid, 0.0)
            price = pt_number(product.get("preco_venda_referencia"))
            units = average * weeks
            revenue = units * price
            if average > 0 and price > 0:
                rows.append({"product": product, "average": average, "units": units, "price": price, "revenue": revenue})
        rows.sort(key=lambda row: row["revenue"], reverse=True)
        for child in self.summary.winfo_children():
            child.destroy()
        cards = [
            (f"Faturamento bruto potencial · {weeks} sem.", fmt_currency(sum(row["revenue"] for row in rows)), "Estimativa, não valor garantido", C["orange"]),
            ("Unidades estimadas", fmt_number(sum(row["units"] for row in rows), 0), f"Média semanal × {weeks}", C["orange_light"]),
            ("Produtos projetados", str(len(rows)), "Com venda histórica e preço", C["yellow"]),
        ]
        for i, info in enumerate(cards):
            self.summary.grid_columnconfigure(i, weight=1, uniform="revenuecards")
            self.stat_card(self.summary, *info).grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else 8, 0))
        for row in rows[:12]:
            product = row["product"]
            self.tree.insert("", "end", values=(
                product.get("produto_id", ""), product.get("nome_produto", ""),
                fmt_number(row["average"]), fmt_number(row["units"], 1),
                fmt_currency(row["price"]), fmt_currency(row["revenue"])
            ))
        if not rows:
            self.tree.insert("", "end", values=("—", "Sem dados suficientes", "", "", "", ""))

    def open_metrics(self):
        window = tk.Toplevel(self)
        window.title("Comparação dos modelos")
        window.geometry("980x640")
        window.configure(bg=C["background"])
        tk.Label(window, text="Como ler as métricas", bg=C["background"], fg=C["text"], font=(FONT, 17, "bold")).pack(anchor="w", padx=20, pady=(18, 4))
        tk.Label(window, text="MAE: em média, quantas unidades a previsão errou por semana. WAPE: o erro total em relação às vendas reais, em porcentagem. Nas duas métricas, menor é melhor.", bg=C["background"], fg=C["muted"], font=(FONT, 9), wraplength=920, justify="left").pack(anchor="w", padx=20, pady=(0, 12))
        wins = self.data.winners
        tk.Label(window, text=f'No teste histórico — RF universal: {wins.get("Random Forest universal", 15)} melhores · Seleção individual: {wins.get("Seleção individual", 16)} melhores · {wins.get("Empate", 41)} empates', bg=C["surface"], fg=C["text"], font=(FONT, 9), padx=12, pady=10, wraplength=920, justify="left").pack(fill="x", padx=20, pady=(0, 12))
        horizon = tk.StringVar(value="4")
        controls = tk.Frame(window, bg=C["background"])
        controls.pack(fill="x", padx=20, pady=(0, 8))
        tk.Label(controls, text="Horizonte", bg=C["background"], fg=C["muted"], font=(FONT, 9)).pack(side="left")
        combo = ttk.Combobox(controls, textvariable=horizon, values=("4", "8"), state="readonly", width=6)
        combo.pack(side="left", padx=8)
        shell, tree = self.table(window, ("sku", "produto", "mae_rf", "mae_sel", "wape_rf", "wape_sel"), ("SKU", "Produto", "Erro RF (unid.)", "Erro individual (unid.)", "Erro RF (%)", "Erro individual (%)"), (75, 210, 145, 170, 120, 150), 12)
        shell.pack(fill="both", expand=True, padx=20, pady=(0, 20))

        def render():
            tree.delete(*tree.get_children())
            rows = [row for row in self.data.diagnostics if str(row.get("horizonte", "")).strip() == horizon.get()]
            rows.sort(key=lambda row: pt_number(row.get("MAE_Random_Forest_universal")), reverse=True)
            for row in rows:
                tree.insert("", "end", values=(
                    row.get("produto_id", ""), row.get("nome_produto", ""),
                    fmt_number(pt_number(row.get("MAE_Random_Forest_universal"))),
                    fmt_number(pt_number(row.get("MAE_Selecao_individual"))),
                    fmt_number(pt_number(row.get("WAPE_pct_Random_Forest_universal"))) + "%",
                    fmt_number(pt_number(row.get("WAPE_pct_Selecao_individual"))) + "%"
                ))
            if not rows:
                tree.insert("", "end", values=("—", "Diagnóstico salvo não encontrado", "", "", "", ""))

        combo.bind("<<ComboboxSelected>>", lambda _e: render())
        render()


class RecommendationsPage(BasePage):
    def __init__(self, app, parent):
        super().__init__(app, parent)
        self.section_title(self, "Recomendações operacionais", "Sugestões baseadas no saldo simulado, estoque mínimo, cobertura e pedidos em aberto.")
        tk.Label(self, text="Se um produto chegar a zero, a sugestão é suspender temporariamente a venda apenas desse produto até a reposição e a recuperação do estoque mínimo. A tela não altera o sistema de vendas; os pedidos são simulados. Cobertura = saldo atual dividido pela média semanal histórica.", bg=C["background"], fg=C["muted"], font=(FONT, 9), wraplength=950, justify="left").pack(anchor="w", pady=(10, 14))
        shell, self.tree = self.table(
            self, ("priority", "sku", "product", "status", "stock", "minimum", "coverage", "order", "due", "action"),
            ("Prioridade", "SKU", "Produto", "Situação", "Saldo", "Mínimo", "Cobertura", "Reposição pendente", "Previsão", "Ação sugerida"),
            (90, 70, 170, 105, 60, 65, 85, 95, 90, 360), 18
        )
        shell.pack(fill="both", expand=True)
        for row in self.data.recommendations():
            tag = "critical" if row["urgencia"] == "Crítico" else "high" if row["urgencia"] in ("Alta", "Atenção") else "normal"
            coverage = "—" if row["cobertura"] is None else fmt_number(row["cobertura"], 1) + " sem."
            self.tree.insert("", "end", tags=(tag,), values=(
                row["urgencia"], row["produto_id"], row["nome_produto"], "Sem saldo" if row["quantidade"] <= 0 else "Suspensão sugerida" if row["status"] == "Pausado" else row["status"],
                row["quantidade"], row["minimo"], coverage, row["pedido"],
                fmt_date(row["data_pedido"]), row["acao"]
            ))


class ProductsPage(BasePage):
    def __init__(self, app, parent):
        super().__init__(app, parent)
        self.query = tk.StringVar()
        self.section_title(self, "Produtos e estoque", "Catálogo ligado ao saldo atual da simulação diária.")
        controls = tk.Frame(self, bg=C["background"])
        controls.pack(fill="x", pady=(12, 10))
        tk.Label(controls, text="Buscar produto", bg=C["background"], fg=C["muted"], font=(FONT, 9)).pack(side="left")
        entry = ttk.Entry(controls, textvariable=self.query, width=34)
        entry.pack(side="left", padx=10)
        entry.bind("<KeyRelease>", lambda _e: self.render_rows())
        shell, self.tree = self.table(
            self, ("sku", "name", "category", "brand", "stock", "minimum", "status"),
            ("SKU", "Produto", "Categoria", "Marca", "Saldo", "Mínimo", "Situação"),
            (80, 240, 150, 120, 75, 75, 140), 20
        )
        shell.pack(fill="both", expand=True)
        self.render_rows()

    def render_rows(self):
        self.tree.delete(*self.tree.get_children())
        query = self.query.get().casefold().strip()
        for row in self.data.inventory.to_dict("records"):
            text = " ".join(str(row.get(col, "")) for col in ("produto_id", "nome_produto", "categoria", "marca")).casefold()
            if query and query not in text:
                continue
            status = str(row["status_atual"])
            display_status = "Sem saldo" if row["quantidade_atual"] <= 0 else "Suspensão sugerida" if status == "Pausado" else status
            tag = "critical" if status == "Pausado" else "high" if status == "Ativo com alerta" else "normal"
            self.tree.insert("", "end", tags=(tag,), values=(
                row.get("produto_id", ""), row.get("nome_produto", ""), row.get("categoria", ""), row.get("marca", ""),
                row["quantidade_atual"], row["estoque_minimo"], display_status
            ))


class SimulationPage(BasePage):
    def __init__(self, app, parent):
        super().__init__(app, parent)
        self.section_title(self, "Simulação diária", "Cenário persistente de prazo de fornecedor de 2 semanas.")
        tk.Label(self, text=f"Atualizado até {fmt_date(self.data.last_update)}. A demanda e os movimentos são simulados e ficam separados do histórico real.", bg=C["background"], fg=C["muted"], font=(FONT, 9), wraplength=930, justify="left").pack(anchor="w", pady=(8, 12))
        chart_panel = tk.Frame(self, bg=C["surface"], highlightbackground=C["border"], highlightthickness=1)
        chart_panel.pack(fill="x", pady=(0, 12))
        tk.Label(chart_panel, text="Demanda solicitada x unidades vendidas", bg=C["surface"], fg=C["text"], font=(FONT, 11, "bold")).pack(anchor="w", padx=14, pady=(12, 0))
        chart = tk.Canvas(chart_panel, height=180, bg=C["surface"], highlightthickness=0)
        chart.pack(fill="x", padx=8, pady=6)
        chart.bind("<Configure>", lambda e: draw_daily_chart(chart, self.data.daily.tail(14), e.width, e.height))
        draw_daily_chart(chart, self.data.daily.tail(14), 900, 180)
        shell, self.tree = self.table(
            self, ("date", "requested", "sold", "unmet", "paused", "open"),
            ("Data", "Solicitadas", "Vendidas", "Não atendidas", "Produtos pausados", "Reposições pendentes"),
            (140, 150, 150, 170, 160, 160), 12
        )
        shell.pack(fill="both", expand=True)
        if self.data.daily.empty:
            self.tree.insert("", "end", values=("Sem registros", "—", "—", "—", "—", "—"))
        else:
            for row in self.data.daily.tail(14).sort_values("data", ascending=False).to_dict("records"):
                self.tree.insert("", "end", values=(
                    fmt_date(row["data"]), int(row["demanda_solicitada_simulada"]), int(row["unidades_vendidas_simuladas"]),
                    int(row["unidades_nao_atendidas_simuladas"]), int(row["produtos_pausados_no_fechamento"]),
                    int(row["pedidos_abertos_no_fechamento"])
                ))


def draw_daily_chart(canvas, data, width, height):
    canvas.delete("all")
    if data is None or data.empty or width < 80 or height < 80:
        canvas.create_text(width / 2, height / 2, text="Aguardando dados diários", fill=C["muted"], font=(FONT, 9))
        return
    rows = data.sort_values("data").tail(14).to_dict("records")
    left, right, top, bottom = 30, width - 18, 18, height - 34
    max_value = max(1, max(float(row.get("unidades_vendidas_simuladas", 0)) for row in rows))
    bar_space = max(8, (right - left) / max(len(rows), 1))
    canvas.create_line(left, bottom, right, bottom, fill=C["border"])
    for i, row in enumerate(rows):
        x = left + (i + 0.5) * bar_space
        sold = float(row.get("unidades_vendidas_simuladas", 0))
        unmet = float(row.get("unidades_nao_atendidas_simuladas", 0))
        bar_w = max(5, min(18, bar_space * 0.42))
        sold_h = (bottom - top) * sold / max_value
        unmet_h = (bottom - top) * unmet / max_value
        canvas.create_rectangle(x - bar_w - 1, bottom - sold_h, x - 1, bottom, fill=C["orange"], outline="")
        if unmet > 0:
            canvas.create_rectangle(x + 1, bottom - unmet_h, x + bar_w + 1, bottom, fill=C["red"], outline="")
        day = pd.Timestamp(row["data"]).strftime("%d/%m")
        if len(rows) <= 10 or i % 2 == 0:
            canvas.create_text(x, bottom + 14, text=day, fill=C["muted"], font=(FONT, 7))
    canvas.create_rectangle(left + 5, 4, left + 14, 12, fill=C["orange"], outline="")
    canvas.create_text(left + 20, 8, anchor="w", text="Vendidas", fill=C["muted"], font=(FONT, 8))
    canvas.create_rectangle(left + 94, 4, left + 103, 12, fill=C["red"], outline="")
    canvas.create_text(left + 109, 8, anchor="w", text="Não atendidas", fill=C["muted"], font=(FONT, 8))


class AdeliaApp(tk.Tk):
    PAGES = {
        "Visão geral": DashboardPage,
        "Previsão": ForecastPage,
        "Recomendações": RecommendationsPage,
        "Produtos": ProductsPage,
        "Simulação diária": SimulationPage,
    }

    def __init__(self):
        super().__init__()
        self.title("ADÉLIA | Sistema Inteligente de Apoio à Decisão")
        self.geometry("1360x860")
        self.minsize(1080, 700)
        self.configure(bg=C["background"])
        self.data = AppData()
        self.data.reload()
        self.configure_styles()
        self._build_shell()
        self.show_page("Visão geral")

    def configure_styles(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("Treeview", background=C["surface"], fieldbackground=C["surface"], foreground=C["text"], rowheight=29, borderwidth=0, font=(FONT, 9))
        style.configure("Treeview.Heading", background=C["surface_alt"], foreground=C["muted"], relief="flat", font=(FONT, 8, "bold"), padding=(8, 8))
        style.map("Treeview", background=[("selected", C["orange_dark"])], foreground=[("selected", C["text"])])
        style.configure("TScrollbar", background=C["surface_alt"], troughcolor=C["surface"], bordercolor=C["surface"], arrowcolor=C["muted"])
        style.configure("TCombobox", fieldbackground=C["surface_alt"], background=C["surface_alt"], foreground=C["text"], arrowcolor=C["orange"], padding=6)
        style.map("TCombobox", fieldbackground=[("readonly", C["surface_alt"])], foreground=[("readonly", C["text"])])
        style.configure("TEntry", fieldbackground=C["surface_alt"], foreground=C["text"], padding=7)

    def _build_shell(self):
        self.sidebar = tk.Frame(self, bg=C["sidebar"], width=246)
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)
        brand = tk.Frame(self.sidebar, bg=C["sidebar"], padx=17, pady=20)
        brand.pack(fill="x")
        try:
            self.logo = tk.PhotoImage(file=str(LOGO_FILE))
            tk.Label(brand, image=self.logo, bg=C["sidebar"]).pack(anchor="w")
        except tk.TclError:
            tk.Label(brand, text="Adélia", bg=C["sidebar"], fg=C["text"], font=(FONT, 28, "bold")).pack(anchor="w")
        tk.Label(brand, text="SISTEMA INTELIGENTE DE APOIO À DECISÃO", bg=C["sidebar"], fg=C["muted"], font=(FONT, 7, "bold"), wraplength=205, justify="left").pack(anchor="w", pady=(6, 0))
        tk.Frame(self.sidebar, bg=C["border"], height=1).pack(fill="x", padx=17, pady=(2, 14))
        tk.Label(self.sidebar, text="MENU", bg=C["sidebar"], fg="#737B7E", font=(FONT, 8, "bold")).pack(anchor="w", padx=20, pady=(0, 8))
        self.nav_buttons = {}
        for title in self.PAGES:
            button = tk.Button(
                self.sidebar, text=title, anchor="w", relief="flat", bd=0,
                bg=C["sidebar"], fg=C["muted"], activebackground=C["surface_alt"], activeforeground=C["text"],
                font=(FONT, 10), padx=18, pady=11, cursor="hand2",
                command=lambda key=title: self.show_page(key)
            )
            button.pack(fill="x", padx=9, pady=2)
            self.nav_buttons[title] = button
        tk.Frame(self.sidebar, bg=C["sidebar"]).pack(fill="both", expand=True)
        tk.Frame(self.sidebar, bg=C["border"], height=1).pack(fill="x", padx=17, pady=(0, 10))
        tk.Label(self.sidebar, text="TCC · Dados sintéticos", bg=C["sidebar"], fg=C["muted"], font=(FONT, 8)).pack(anchor="w", padx=18, pady=(0, 16))

        self.main = tk.Frame(self, bg=C["background"])
        self.main.pack(side="left", fill="both", expand=True)
        header = tk.Frame(self.main, bg=C["background"], padx=24, pady=17)
        header.pack(fill="x")
        self.title_label = tk.Label(header, text="", bg=C["background"], fg=C["text"], font=(FONT, 20, "bold"))
        self.title_label.pack(side="left")
        self.update_label = tk.Label(header, text="", bg=C["background"], fg=C["muted"], font=(FONT, 8))
        self.update_label.pack(side="left", padx=(12, 0), pady=(7, 0))
        refresh = tk.Button(
            header, text="Atualizar dados", command=self.refresh_data, relief="flat", bd=0,
            bg=C["surface_alt"], fg=C["text"], activebackground=C["orange_dark"], activeforeground=C["text"],
            font=(FONT, 9, "bold"), padx=12, pady=8, cursor="hand2"
        )
        refresh.pack(side="right")
        self.page_container = tk.Frame(self.main, bg=C["background"], padx=24)
        self.page_container.pack(fill="both", expand=True, pady=(0, 22))

    def show_page(self, key):
        self.current_page = key
        self.title_label.config(text=key)
        self.update_label.config(text=f"Atualizado até {fmt_date(self.data.last_update)}")
        for name, button in self.nav_buttons.items():
            active = name == key
            button.config(bg=C["surface_alt"] if active else C["sidebar"], fg=C["text"] if active else C["muted"], font=(FONT, 10, "bold" if active else "normal"))
        for child in self.page_container.winfo_children():
            child.destroy()
        self.PAGES[key](self, self.page_container).pack(fill="both", expand=True)

    def refresh_data(self):
        try:
            self.data.reload()
            self.show_page(self.current_page)
        except Exception as error:
            messagebox.showerror("Não foi possível atualizar", str(error))


def main():
    app = AdeliaApp()
    app.mainloop()


if __name__ == "__main__":
    main()


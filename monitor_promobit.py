import json
import os
import re
import time
from dataclasses import dataclass
from typing import List, Optional, Set
from bs4 import BeautifulSoup
import requests

# Variáveis injetadas pelo ambiente do GitHub Actions
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
CACHE_FILE = "seen_deals.json"


@dataclass
class NotebookDeal:
    title: str
    price: float
    url: str
    matched_reasons: List[str]


def send_telegram(deal: NotebookDeal) -> bool:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[!] Erro: TELEGRAM_BOT_TOKEN ou TELEGRAM_CHAT_ID não definidos.")
        return False

    message = (
        f"🎯 *Notebook Encontrado!*\n\n"
        f"💻 *{deal.title}*\n"
        f"💰 *Preço:* R$ {deal.price:,.2f}\n"
        f"🔍 *Critérios:* {', '.join(deal.matched_reasons)}\n\n"
        f"🔗 [Ver no Promobit]({deal.url})"
    )

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown",
        "disable_web_page_preview": False,
    }

    try:
        res = requests.post(url, json=payload, timeout=10)
        return res.status_code == 200
    except Exception as e:
        print(f"[!] Falha na requisição ao Telegram: {e}")
        return False


class PromobitMonitor:
    def __init__(self, max_price: float = 9000.0):
        self.max_price = max_price
        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
        }

        self.target_models = [
            r"zenbook",
            r"galaxy book4 pro",
            r"galaxy book 4 pro",
            r"yoga slim",
            r"yoga 7",
            r"thinkpad",
            r"macbook air",
        ]

        self.target_cpus = [
            r"core ultra",
            r"ultra 7",
            r"ultra 5",
            r"256v",
            r"258v",
            r"ryzen 7",
            r"8840hs",
            r"8845hs",
            r"7840u",
            r"m2",
            r"m3",
            r"m4",
        ]

        self.exclusion_keywords = [
            r"celeron",
            r"pentium",
            r"core i3",
            r"ryzen 3",
            r"nitro 5",
            r"tuf gaming",
            r"loq",
            r"alienware",
            r"predator",
            r"positivo",
            r"multilaser",
        ]

        self.seen_deals: Set[str] = self._load_cache()

    def _load_cache(self) -> Set[str]:
        if os.path.exists(CACHE_FILE):
            try:
                with open(CACHE_FILE, "r", encoding="utf-8") as f:
                    return set(json.load(f))
            except Exception:
                return set()
        return set()

    def _save_cache(self):
        try:
            with open(CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump(list(self.seen_deals), f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[!] Erro ao salvar cache: {e}")

    def _parse_price(self, raw_text: str) -> Optional[float]:
        clean_text = raw_text.replace(".", "").replace(",", ".")
        match = re.search(r"(\d+\.?\d*)", clean_text)
        if match:
            try:
                return float(match.group(1))
            except ValueError:
                return None
        return None

    def evaluate_deal(self, title: str, price: float) -> tuple[bool, List[str]]:
        title_lower = title.lower()
        reasons = []

        if price > self.max_price or price <= 0:
            return False, []

        for exc in self.exclusion_keywords:
            if re.search(exc, title_lower):
                return False, []

        if re.search(r"\b8\s?gb\b", title_lower) and not re.search(
            r"\b(16|32)\s?gb\b", title_lower
        ):
            return False, []

        for model in self.target_models:
            if re.search(model, title_lower):
                reasons.append(f"Modelo: {model.upper()}")
                break

        for cpu in self.target_cpus:
            if re.search(cpu, title_lower):
                reasons.append(f"CPU: {cpu.upper()}")
                break

        if re.search(r"\b(16|32)\s?gb\b", title_lower):
            reasons.append("RAM >= 16GB")

        is_valid = len(reasons) >= 1
        return is_valid, reasons

    def run(self, pages: int = 3):
        print("Iniciando varredura...")
        new_alerts = 0

        for page in range(1, pages + 1):
            url = f"https://www.promobit.com.br/promocoes/informatica/notebooks/?pagina={page}"
            try:
                response = requests.get(url, headers=self.headers, timeout=12)
                if response.status_code != 200:
                    continue

                soup = BeautifulSoup(response.text, "html.parser")
                deal_cards = soup.find_all(
                    ["article", "div"],
                    class_=lambda c: c and "deal" in str(c).lower(),
                )
                if not deal_cards:
                    deal_cards = soup.find_all("a", href=re.compile(r"/oferta/"))

                for card in deal_cards:
                    text_content = card.get_text(separator=" ")
                    title_elem = card.find(
                        ["h2", "h3", "span"],
                        class_=lambda c: c and "title" in str(c).lower(),
                    )
                    title = title_elem.get_text().strip() if title_elem else text_content[:100]

                    link_elem = card if card.name == "a" else card.find("a", href=True)
                    relative_url = link_elem["href"] if link_elem else ""
                    deal_url = (
                        f"https://www.promobit.com.br{relative_url}"
                        if relative_url.startswith("/")
                        else relative_url
                    )

                    price_match = re.search(r"R\$\s*([\d\.,]+)", text_content)
                    if not price_match:
                        continue

                    parsed_price = self._parse_price(price_match.group(1))
                    if not parsed_price:
                        continue

                    deal_id = deal_url.split("?")[0] if deal_url else f"{title}-{parsed_price}"

                    if deal_id in self.seen_deals:
                        continue

                    valid, reasons = self.evaluate_deal(title, parsed_price)
                    if valid:
                        deal = NotebookDeal(
                            title=title,
                            price=parsed_price,
                            url=deal_url,
                            matched_reasons=reasons,
                        )
                        print(f"[+] Enviando alerta: {deal.title} (R$ {deal.price})")
                        if send_telegram(deal):
                            self.seen_deals.add(deal_id)
                            new_alerts += 1

                time.sleep(1.5)
            except Exception as e:
                print(f"[!] Erro ao varrer página {page}: {e}")

        self._save_cache()
        print(f"Finalizado. Mensagens enviadas: {new_alerts}")


if __name__ == "__main__":
    monitor = PromobitMonitor(max_price=9000.0)
    monitor.run(pages=3)
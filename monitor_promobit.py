from dataclasses import dataclass
import json
import os
import re
from typing import List, Set
from bs4 import BeautifulSoup
import requests
import html  


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

    preco_str = f"R$ {deal.price:,.2f}" if deal.price > 0 else "Ver no link"
    
    # Escapa caracteres especiais para não quebrar o HTML do Telegram
    titulo_seguro = html.escape(deal.title)
    criterios_seguro = html.escape(", ".join(deal.matched_reasons))
    url_segura = html.escape(deal.url, quote=True)

    message = (
        f"🎯 <b>Notebook Encontrado!</b>\n\n"
        f"💻 <b>{titulo_seguro}</b>\n"
        f"💰 <b>Preço:</b> {preco_str}\n"
        f"🔍 <b>Critérios:</b> {criterios_seguro}\n\n"
        f"🔗 <a href=\"{url_segura}\">Ver Oferta no Promobit</a>"
    )

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }

    try:
        res = requests.post(url, json=payload, timeout=10)
        if res.status_code != 200:
            print(f"[!] Erro Telegram ({res.status_code}): {res.text}")
        return res.status_code == 200
    except Exception as e:
        print(f"[!] Falha na requisição ao Telegram: {e}")
        return False


class PromobitMonitor:

    def __init__(self, max_price: float = 5000.0):
        self.max_price = max_price
        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/129.0.0.0 Safari/537.36"
            ),
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
            ),
            "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8",
        }

        self.urls = [
            "https://www.promobit.com.br/",
        ]

        self.target_models = [  
            r"zenbook",
            r"galaxy book4",
            r"galaxy book 4",
            r"ideapad slim",
            r"vivobook",
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
            r"7735hs",
            r"7730u",
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
            r"mochila",
            r"suporte",
            r"base cooler",
            r"carregador",
            r"fonte",
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

    def _extract_offers_from_next_data(self, obj, results: list):
        """Percorre recursivamente o JSON interno do Next.js buscando objetos de oferta."""
        if isinstance(obj, dict):
            title = obj.get("offerTitle") or obj.get("title")
            slug = obj.get("offerSlug") or obj.get("slug")
            price = obj.get("offerPrice") or obj.get("price")

            if title and slug and isinstance(title, str) and len(title) > 10:
                full_url = (
                    slug
                    if slug.startswith("http")
                    else f"https://www.promobit.com.br/oferta/{slug.lstrip('/')}"
                )
                try:
                    price_val = float(price) if price is not None else 0.0
                except (ValueError, TypeError):
                    price_val = 0.0

                results.append({"title": title, "price": price_val, "url": full_url})

            for val in obj.values():
                self._extract_offers_from_next_data(val, results)

        elif isinstance(obj, list):
            for item in obj:
                self._extract_offers_from_next_data(item, results)

    def _extract_offers(self, html: str) -> list:
        soup = BeautifulSoup(html, "html.parser")
        offers = []

        # 1. Tenta extrair do JSON estruturado do Next.js (__NEXT_DATA__)
        next_script = soup.find("script", id="__NEXT_DATA__")
        if next_script and next_script.string:
            try:
                data = json.loads(next_script.string)
                self._extract_offers_from_next_data(data, offers)
            except Exception as e:
                print(f"[!] Aviso ao ler __NEXT_DATA__: {e}")

        # 2. Fallback: busca links de oferta diretamente no HTML
        if not offers:
            for a in soup.find_all("a", href=re.compile(r"/oferta/")):
                text = a.get_text(" ", strip=True)
                href = a.get("href", "")
                if len(text) > 15:
                    full_url = (
                        href
                        if href.startswith("http")
                        else f"https://www.promobit.com.br{href}"
                    )
                    price_match = re.search(r"R\$\s*([\d\.]+,\d{2}|[\d\.]+)", text)
                    price_val = 0.0
                    if price_match:
                        try:
                            price_val = float(
                                price_match.group(1).replace(".", "").replace(",", ".")
                            )
                        except ValueError:
                            pass
                    offers.append({"title": text, "price": price_val, "url": full_url})

        # Remove duplicatas mantendo a ordem
        unique_offers = []
        seen_urls = set()
        for o in offers:
            clean_url = o["url"].split("?")[0]
            if clean_url not in seen_urls:
                seen_urls.add(clean_url)
                o["url"] = clean_url
                unique_offers.append(o)

        return unique_offers

    def evaluate_deal(self, title: str, price: float) -> tuple[bool, List[str]]:
        full_text = title.lower()
        reasons = []

        if price > 0 and price > self.max_price:
            return False, []

        for exc in self.exclusion_keywords:
            if re.search(exc, full_text):
                return False, []

        # Só exige 16GB+ quando sair do modo de teste genérico ("notebook")
        if r"notebook" not in self.target_models:
            if re.search(r"\b8\s?gb\b", full_text) and not re.search(
                r"\b(16|32)\s?gb\b", full_text
            ):
                return False, []

        for model in self.target_models:
            if re.search(model, full_text):
                reasons.append(f"Modelo: {model.upper()}")
                break

        for cpu in self.target_cpus:
            if re.search(cpu, full_text):
                reasons.append(f"CPU: {cpu.upper()}")
                break

        if re.search(r"\b(16|32)\s?gb\b", full_text):
            reasons.append("RAM >= 16GB")

        return len(reasons) >= 1, reasons

    def run(self):
        print("Iniciando varredura no Promobit...")
        new_alerts = 0

        for page_url in self.urls:
            try:
                print(f"Consultando: {page_url}")
                response = requests.get(page_url, headers=self.headers, timeout=15)
                print(
                    f"Status HTTP: {response.status_code} | Tamanho: {len(response.text)} bytes"
                )

                if response.status_code != 200:
                    continue

                offers = self._extract_offers(response.text)
                print(f"Ofertas extraídas da página: {len(offers)}")

                for offer in offers:
                    deal_id = offer["url"]
                    if deal_id in self.seen_deals:
                        continue

                    valid, reasons = self.evaluate_deal(offer["title"], offer["price"])
                    if valid:
                        deal = NotebookDeal(
                            title=offer["title"],
                            price=offer["price"],
                            url=offer["url"],
                            matched_reasons=reasons,
                        )
                        print(f"[+] Enviando alerta: {deal.title}")
                        if send_telegram(deal):
                            self.seen_deals.add(deal_id)
                            new_alerts += 1


            except Exception as e:
                print(f"[!] Erro ao consultar {page_url}: {e}")

        self._save_cache()
        print(f"Finalizado. Mensagens enviadas: {new_alerts}")


if __name__ == "__main__":
    monitor = PromobitMonitor(max_price=5000.0)
    monitor.run()

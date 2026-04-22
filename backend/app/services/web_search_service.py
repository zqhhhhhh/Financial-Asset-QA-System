"""Web search service using DuckDuckGo (no API key required)."""
import logging

from ddgs import DDGS

logger = logging.getLogger(__name__)


class WebSearchService:
    def search(
        self,
        query: str,
        max_results: int = 8,
        region: str = "cn-zh",
    ) -> list[dict]:
        """
        Search the web and return a list of results.
        Each result: {title, url, snippet}
        """
        try:
            with DDGS() as ddgs:
                raw = list(ddgs.text(query, max_results=max_results, region=region))
            results = [
                {"title": r.get("title", ""), "url": r.get("href", ""), "snippet": r.get("body", "")}
                for r in raw
                if r.get("body")
            ]
            logger.info("[web_search] query=%r → %d results", query, len(results))
            return results
        except Exception as exc:
            logger.warning("[web_search] failed: %s", exc)
            return []

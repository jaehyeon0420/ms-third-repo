import asyncio
import aiohttp
import re
import os
from typing import List, Dict, Any, Optional
from src.model.schema import Precedent
from src.graph.state import GraphState
from src.configs import model_config
from src.utils.logger import get_logger
from src.tools.web_search import fetch_precedent_list, fetch_precedent_detail

logger = get_logger(__name__)

async def web_search_node(state: GraphState) -> Dict[str, Any]:
    """
    웹 검색 노드 (법령정보센터 API 연동)
    grade_precedents_node에서 결정된 검색어에서 추출된 web_search_keywords를 사용
    """
    try:
        keywords = state.get("web_search_keywords", [])
        web_search_count = state.get("web_search_count", 0)
        
        # 키워드가 없으면 기존 쿼리를 fallback으로 사용하거나 빈 리스트 처리
        if not keywords:
            logger.warning("[웹 검색] 검색 키워드가 없어 검색을 건너뜁니다.")
            return {
                "retrieved_precedents": [],
                "web_search_count": web_search_count + 1
            }
        
        logger.info(f"[웹 검색] 시작: 키워드={keywords} (시도 {web_search_count + 1}회)")
        
        async with aiohttp.ClientSession() as session:
            # 판례 목록 조회
            prec_ids = await fetch_precedent_list(session, keywords)
            
            if not prec_ids:
                logger.info("[웹 검색] 외부 API에서 판례를 찾지 못했습니다.")
                return {
                    "retrieved_precedents": [],
                    "web_search_count": web_search_count + 1
                }
                
            # 판례 본문 조회 (병렬 처리)
            logger.info(f"[웹 검색] 상세 본문 조회 시작 ({len(prec_ids)}건)")
            tasks = [fetch_precedent_detail(session, pid) for pid in prec_ids]
            results = await asyncio.gather(*tasks)
                    
            # None 제외 및 유효한 결과만 필터링
            precedents = [r for r in results if r is not None]
            
        logger.info(f"[웹 검색] 완료: 유효한 판례 {len(precedents)}건 확보")
        
        # 검색 결과 반환하여, 다시 판례 검증 노드로 라우팅
        return {
            "retrieved_precedents": precedents,
            "web_search_count": web_search_count + 1,
        }
    except Exception as e:
        logger.error(f"[웹 검색] 노드 실행 중 오류: {e}", exc_info=True)
        return {"retrieved_precedents": [], "web_search_count": 0}

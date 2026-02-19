from typing import Dict, Any
from src.graph.state import GraphState
from src.utils.format import extract_common_context, extract_precedent_context
from src.utils.logger import get_logger
from src.services.report import generate_report, evaluate_report

logger = get_logger(__name__)

async def generate_report_node(state: GraphState) -> Dict[str, Any]:
    """Qwen-3-8B: 보고서 생성 (vLLM API 호출)"""
    try:
        c_tm = state["current_collected_trademark"]
        p_tm = state["protection_trademark"]
        
        logger.info(f"[보고서 생성] 시작: {p_tm.p_trademark_name} vs {c_tm.c_trademark_name}")
        
        # 컨텍스트 조립 (refined_precedents가 있으면 우선 사용)
        context = {**extract_common_context(state), **extract_precedent_context(state, use_refined=True)}
        
        cleaned_content = generate_report(context)
        
        logger.info(f"[보고서 생성] 완료: {len(cleaned_content)}자")
        
        return {"report_content": cleaned_content}
        
    except Exception as e:
        logger.error(f"[보고서 생성] 오류 발생: {e}", exc_info=True)
        return {"report_content": "보고서 생성 실패: 시스템 오류가 발생했습니다."}


async def evaluate_report_node(state: GraphState) -> Dict[str, Any]:
    """GPT-4o Agent: 보고서 평가"""
    try:
        logger.info("[보고서 평가] 품질 검증 시작")
        
        report_content = state.get("report_content", "")
        regeneration_count = state.get("regeneration_count", 0)
                
        # 평가용 컨텍스트: 원본 데이터 + 생성된 보고서
        context = {**extract_common_context(state), **extract_precedent_context(state, use_refined=True)}
        
        result = evaluate_report(context, report_content)
                      
        logger.info(f"[보고서 평가] 결과: {result.decision} (점수: {result.score})")
        logger.info(f"피드백: {result.feedback}")
        
        # 재생성 시도 카운트 증가
        if result.decision == "regenerate":
            regeneration_count += 1
            logger.info(f"보고서 재생성 카운트 증가 ({regeneration_count-1} -> {regeneration_count})")
        
        return {
            "evaluation_score": result.score,
            "evaluation_feedback": result.feedback,
            "evaluation_decision": result.decision,
            "regeneration_count": regeneration_count
        }
    except Exception as e:
        logger.error(f"[보고서 평가] 오류 발생: {e}", exc_info=True)
        return {
            "evaluation_score": 0.0,
            "evaluation_decision": "regenerate",
            "evaluation_feedback": "Error parsing output"
        }

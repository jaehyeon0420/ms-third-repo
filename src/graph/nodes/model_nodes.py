from typing import Dict, Any
from src.graph.state import GraphState
from src.services.visual_scoring import calculate_visual_similarity
from src.services.phonetic_scoring import calculate_phonetic_similarity
from src.services.conceptual_scoring import calculate_conceptual_similarity
from src.services.ensemble import calculate_risk
from src.utils.logger import get_logger
from src.container import Container

logger = get_logger(__name__)

def visual_similarity(state: GraphState) -> Dict[str, Any]:
    """시각적 유사도 분석"""
    try:
        p_tm = state["protection_trademark"]
        c_tm = state["current_collected_trademark"]
        
        logger.info(f"[시각적 유사도] 분석 시작: {p_tm.p_trademark_name} vs {c_tm.c_trademark_name}")
        
        score = calculate_visual_similarity(
            p_tm.p_trademark_image_vec, 
            c_tm.c_trademark_image_vec
        )
        logger.info(f"[시각적 유사도] 분석 완료: 점수={score:.4f}")
        return {"visual_similarity_score": score}
    except Exception as e:
        logger.error(f"[시각적 유사도] 오류 발생: {e}", exc_info=True)
        return {"visual_similarity_score": 0.0}

def phonetic_similarity(state: GraphState) -> Dict[str, Any]:
    """발음적 유사도 분석"""
    try:
        p_tm = state["protection_trademark"]
        c_tm = state["current_collected_trademark"]
        
        logger.info(f"[발음적 유사도] 분석 시작: {p_tm.p_trademark_name} vs {c_tm.c_trademark_name}")
        
        score = calculate_phonetic_similarity(
            p_tm.p_trademark_name, 
            c_tm.c_trademark_name
        )
        logger.info(f"[발음적 유사도] 분석 완료: 점수={score:.4f}")
        
        return {"phonetic_similarity_score": score}
    except Exception as e:
        logger.error(f"[발음적 유사도] 오류 발생: {e}", exc_info=True)
        return {"phonetic_similarity_score": 0.0}

def conceptual_similarity(state: GraphState) -> Dict[str, Any]:
    """관념적 유사도 분석"""
    try:
        p_tm = state["protection_trademark"]
        c_tm = state["current_collected_trademark"]
        
        logger.info(f"[관념적 유사도] 분석 시작: {p_tm.p_trademark_name} vs {c_tm.c_trademark_name}")
        
        dict_result = calculate_conceptual_similarity(p_tm, c_tm)
        
        score = dict_result.get("score", 0.0)
        logger.info(f"[관념적 유사도] 분석 완료: 점수={score:.4f}")
        
        return {
            "conceptual_similarity_score": score, 
            "conceptual_description": dict_result.get("p_description", "")
        }
    except Exception as e:
        logger.error(f"[관념적 유사도] 오류 발생: {e}", exc_info=True)
        return {"conceptual_similarity_score": 0.0, "conceptual_description": ""}

async def ensemble_model(state: GraphState) -> Dict[str, Any]:
    """앙상블 모델"""
    try:
        logger.info("[앙상블 모델] 위험도 평가 시작")
        
        ensemble_result = await calculate_risk(
            state["protection_trademark"],
            state["current_collected_trademark"],
            state["visual_similarity_score"],
            state["phonetic_similarity_score"],
            state["conceptual_similarity_score"],
            state["conceptual_description"]
        )
        
        # 위험도가 일정 수준 이상이면 침해 발견 플래그 설정
        is_infringement = ensemble_result.risk_level in ["H", "M", "L"]
        
        logger.info(f"[앙상블 모델] 평가 완료: 위험등급={ensemble_result.risk_level}, 총점={ensemble_result.total_score:.2f}, 침해여부={is_infringement}")
        
        return {
            "visual_similarity_score": ensemble_result.visual_score,
            "visual_weight": ensemble_result.visual_weight,
            "phonetic_similarity_score": ensemble_result.phonetic_score,
            "phonetic_weight": ensemble_result.phonetic_weight,
            "conceptual_similarity_score": ensemble_result.conceptual_score,
            "conceptual_weight": ensemble_result.conceptual_weight,
            "ensemble_result": ensemble_result,
            "is_infringement_found": is_infringement
        }
    except Exception as e:
        logger.error(f"[앙상블 모델] 오류 발생: {e}", exc_info=True)
        return {"is_infringement_found": False, "ensemble_result": None}


async def save_infringe_risk_node(state: GraphState) -> Dict[str, Any]:
    """위험군 저장 노드"""
    try:
        vector_store = Container.get_vector_store()
        
        # 앙상블 모델 결과
        ensemble_result = state.get("ensemble_result")
        total_score = ensemble_result.total_score
        risk_level = ensemble_result.risk_level
        
        # 보호 상표 번호
        p_trademark_reg_no = state["protection_trademark"].p_trademark_reg_no
        
        logger.info(f"[위험군 저장] 조건 확인: 총점={total_score}, 위험도={risk_level}")
        
        # 결과 재검증
        if risk_level in ["H", "M", "L"]:
            # 수집 상표 정보, 앙상블 모델 결과, 보호 상표 번호
            risk_data = {
                "c_tm": state["current_collected_trademark"],
                "ensemble_result": state["ensemble_result"],
                "p_trademark_reg_no": p_trademark_reg_no
            }
            
            # INSERT tbl_infringe_risk 
            logger.info("[위험군 저장] DB 저장 시작")
            await vector_store.save_infringe_risk(risk_data)
            logger.info("[위험군 저장] 완료")
        else:
            logger.info("[위험군 저장] 조건 미달로 저장하지 않음")
        
        return {}
    except Exception as e:
        logger.error(f"[위험군 저장] 오류 발생: {e}", exc_info=True)
        return {}
from typing import Literal
from langgraph.graph import StateGraph, END
from src.graph.state import GraphState
from src.configs import model_config

from src.graph.nodes.model_nodes import visual_similarity, phonetic_similarity, conceptual_similarity, ensemble_model, save_infringe_risk_node
from src.graph.nodes.web_search_nodes import web_search_node
from src.graph.nodes.precedent_nodes import generate_query_node, grade_precedents_node , retrieve_precedents_node
from src.graph.nodes.report_nodes import generate_report_node, evaluate_report_node


def check_infringement(state: GraphState) -> Literal["save_risk", "end"]:
    """
    앙상블 모델 결과에 따른 진행 여부 결정
    - 위험군(H, M, L)으로 판명되면 저장 단계로 진행
    - 안전(Safe)으로 판명되면 워크플로우 종료
    """
    ensemble_result = state.get("ensemble_result")
    risk_level = ensemble_result.risk_level
    
    if risk_level in ["H", "M", "L"]:
        return "save_risk"
    return "end"

def route_after_grading(state: GraphState) -> Literal["generate_report", "generate_query", "web_search"]:
    """
    판례 검증 후 분기
    grade_precedents_node가 State에 기록한 grading_decision 값을 노드명에 매핑
    """
    decision = state.get("grading_decision", "approved")

    routing_map = {
        "approved": "generate_report",
        "rewrite": "generate_query",
        "web_search": "web_search",
    }

    return routing_map.get(decision, "generate_report")

def route_after_evaluation(state: GraphState) -> Literal["generate_query", "generate_report", "end"]:
    """
    보고서 평가 후 분기
    - approved (score >= 80) -> LangGraph END
    - regenerate -> 보고서 재생성
    """
    decision = state.get("evaluation_decision", "approved")
    score = state.get("evaluation_score")
    
    regeneration_count = state.get("regeneration_count")
    rewrite_count = state.get("rewrite_count")
    
    # 보고서 최대 재생성 횟수, 쿼리 최대 재생성 횟수, 보고서 평가 점수 임계값
    retry_config = model_config.get('retry', {})
    MAX_REGEN = retry_config.get("max_regeneration_report_count")
    MAX_REWRITE = retry_config.get("max_rewrite_query_count")
    THRESHOLD = retry_config.get("report_evaluation_threshold")

    # 보고서 평가 점수 임계값 초과 또는 승인 시 종료
    if decision == "approved" or score >= THRESHOLD:
        return "end"
        
    # 보고서 재생성 시도 (보고서 평가 점수 미달 & 재생성 횟수 남음)
    if decision == "regenerate" and regeneration_count < MAX_REGEN:
        return "generate_report"
        
    # 쿼리 재생성 시도 (보고서 평가 점수 미달 & 쿼리 재생성 횟수 남음)
    if decision == "regenerate" and rewrite_count < MAX_REWRITE:
        return "generate_query"
        
    return "end"


workflow = StateGraph(GraphState)

def start_node(state: GraphState):
    """
    시작 노드 (Fan-out 분기점)
    - 시각, 발음, 관념 유사도를 병렬로 실행하기 위한 진입점
    - 보호 상표와 수집 상표는 진입 이전에 조회되어 State에 저장되어 있어야 함
    """
    return {}

# 시작 노드
workflow.add_node("start", start_node)

# 외관, 발음, 관념 유사도
workflow.add_node("visual_similarity", visual_similarity)
workflow.add_node("phonetic_similarity", phonetic_similarity)
workflow.add_node("conceptual_similarity", conceptual_similarity)
# 앙상블
workflow.add_node("ensemble_model", ensemble_model)

# 침해 위험 상표 저장
workflow.add_node("save_risk", save_infringe_risk_node)

# 판례 검색 Query 생성, 판례 검색, 판례 검증, 웹 검색
workflow.add_node("generate_query", generate_query_node)
workflow.add_node("retrieve_precedents", retrieve_precedents_node)
workflow.add_node("grade_precedents", grade_precedents_node )
workflow.add_node("web_search", web_search_node)

# 보고서 생성, 보고서 평가
workflow.add_node("generate_report", generate_report_node)
workflow.add_node("evaluate_report", evaluate_report_node)


# 시각, 발음, 관념 병렬 처리
workflow.set_entry_point("start")
workflow.add_edge("start", "visual_similarity")
workflow.add_edge("start", "phonetic_similarity")
workflow.add_edge("start", "conceptual_similarity")

# 앙상블 모델
workflow.add_edge("visual_similarity", "ensemble_model")
workflow.add_edge("phonetic_similarity", "ensemble_model")
workflow.add_edge("conceptual_similarity", "ensemble_model")

# 앙상블 모델 -> 침해 위험 상표 저장
workflow.add_conditional_edges(
    "ensemble_model",
    check_infringement,
    {
        "save_risk": "save_risk",
        "end": END
    }
)

# 침해 위험 상표 저장 -> 판례 검색 Query 생성
workflow.add_edge("save_risk", "generate_query")

# 판례 검색 Query 생성 -> 판례 검색
workflow.add_edge("generate_query", "retrieve_precedents")

# 판례 검색 -> 판례 검증
workflow.add_edge("retrieve_precedents", "grade_precedents")

# 판례 검증 -> 보고서 생성, 판례 검색 Query 생성, 웹 검색 분기
workflow.add_conditional_edges(
    "grade_precedents", # 판례 검증
    route_after_grading,
    {
        "generate_report": "generate_report", # 보고서 생성
        "generate_query": "generate_query",   # 판례 검색 Query 재생성
        "web_search": "web_search"            # 웹 검색
    }
)

# 웹 검색 -> 판례 재검증
workflow.add_edge("web_search", "grade_precedents")

# 보고서 생성 -> 보고서 평가
workflow.add_edge("generate_report", "evaluate_report")

# 보고서 평가 -> 판례 검색 Query 생성 분기, 보고서 생성 분기
workflow.add_conditional_edges(
    "evaluate_report", # 보고서 평가
    route_after_evaluation,
    {
        "generate_query": "generate_query",    # 판례 검색 Query 재생성
        "generate_report": "generate_report",  # 보고서 생성
        "end": END                             # 종료
    }
)

app = workflow.compile()
from typing import List, Optional, Literal
from pydantic import BaseModel, Field
from datetime import datetime

class ProtectionTrademarkInfo(BaseModel):
    """보호 상표 정보"""
    p_trademark_reg_no : str
    p_trademark_name : str 
    p_trademark_type : str
    p_trademark_class_code : str
    p_trademark_image : str 
    p_trademark_image_vec : list[float]
    p_trademark_user_no : int
    p_product_kinds : str

class CollectedTrademarkInfo(BaseModel):
    """수집 상표 정보"""
    c_trademark_no : int    
    c_product_name : str
    c_product_page_url : str
    c_manufacturer_info : str
    c_brand_info : str
    c_l_category : str
    c_m_category : str
    c_s_category : str
    c_trademark_type : str
    c_trademark_class_code : str
    c_trademark_name : str
    c_trademark_name_vec : list[float]
    c_trademark_image : str
    c_trademark_image_vec : list[float]
    c_trademark_ent_date : datetime
    

class InfringementRisk(BaseModel):
    """침해 위험 분석 결과"""
    visual_score: float
    visual_weight: float
    phonetic_score: float
    phonetic_weight: float
    conceptual_score: float
    conceptual_weight: float
    total_score: float
    risk_level: str
    visual_description: str

class ReasonTrademark(BaseModel):
    """거절 사유 정보"""
    patent_id:str
    similarity_score:float
    cleaned_content:str
    reason_tags:str
    product_tags:str
    
class Precedent(BaseModel):
    """판례 정보"""
    precedent_no: str
    file_name : str | None = None
    case_id : str | None = None
    start_page : str | None = None
    content: str
    is_relevant: bool # 적합성 여부
    
    
class EvaluationResult(BaseModel):
    """보고서 평가 결과"""
    score: float = Field(description="보고서 평가 점수 (0~100)")
    feedback: str = Field(description="구체적인 평가 피드백 및 수정 제안")
    decision: Literal["approved", "regenerate"] = Field(description="보고서 평가 점수 : 70점 이상='approved', 70점 미만='regenerate'")    

class ApprovedReport(BaseModel):
    """승인된 보고서 정보 (메일 발송용)"""
    c_trademark_name: str       # 수집 상표명
    c_trademark_image: str      # 수집 상표 이미지
    report_content: str         # 보고서 내용
    risk_level: str             # 위험도 (H, M)
    total_score: float          # 종합 점수

class JudgeDecision(BaseModel):
    """판례 적합성 판단 및 후속 작업 결정 모델"""
    
    decision: Literal["approve", "rewrite", "web_search"] = Field(
        description="판례가 적합하면 approve, 부족하여 쿼리를 고쳐야 하면 rewrite, 외부 검색이 필요하면 web_search"
    )
    
    reasoning: List[str] = Field(
        description="채택된 각 판례에 대한 구체적 법리 근거 (인덱스 목록과 순서 일치 필수)"
    )
    
    relevant_indices: List[int] = Field(
        default=[], 
        description="approve일 때만 사용. 적합한 판례의 0-based 인덱스 목록"
    )
    
    feedback_or_query: Optional[str] = Field(
        default=None,
        description="rewrite일 때는 쿼리 재생성 피드백, web_search일 때는 웹 검색 키워드 리스트"
    )

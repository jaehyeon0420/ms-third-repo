import json
import base64
from typing import List, Dict, Any, Optional
from src.utils.db import Database
from src.model.schema import Precedent, ReasonTrademark
from src.configs import model_config
from src.utils.logger import get_logger

logger = get_logger(__name__)

class VectorStore:
    """PostgreSQL pgvector 연동 클래스"""
    
    def __init__(self):
        # DB 연결은 Database.get_pool()을 통해 전역 풀을 사용하므로
        # 여기서는 별도의 초기화가 필요 없을 수 있음
        pass
        
    async def search_similar_trademarks(self) -> List[Dict[str, Any]]:
        """
        유사 상표 검색 (배치 시작점)
        1. 보호 상표 전체 조회
        2. 각 보호 상표에 대해 수집 상표 벡터 검색 (Cosine Distance)
        3. 유사도 threshold 이상인 쌍 반환
        """
        try:
            pool = await Database.get_pool()
            results = []
            
            # pgvector 거리 계산: 1 - cosine_similarity = cosine_distance (<=> operator)
            # similarity >= threshold  ==>  1 - distance >= threshold  ==>  distance <= 1 - threshold
            sim_threshold = model_config.get('db', {}).get('similar_trademark_threshold', 0.8)
            distance_threshold = 1.0 - sim_threshold
            
            logger.info(f"[DB] 유사 상표 검색 시작 (기준 유사도: {sim_threshold:.2f})")
            
            async with pool.acquire() as conn:
                # 1. 관리중인 보호 상표 전체 조회
                p_query = """
                    SELECT a.p_trademark_reg_no,
                           a.p_trademark_name, 
                           a.p_trademark_type, 
                           a.p_trademark_class_code, 
                           a.p_trademark_image, 
                           a.p_trademark_user_no,
                           a.p_trademark_name_vec, 
                           a.p_trademark_image_vec,
                           string_agg(distinct b.product_name, ', ' order by b.product_name) as p_product_kinds
                      FROM tbl_protection_trademark a,
                           tbl_p_trademark_product b
                     where a.p_trademark_reg_no = b.p_trademark_reg_no 
                       and a.manage_end_date is null
                       and exists (select 1 
                                     from tbl_customer_info z 
                                    where z.customer_no = a.p_trademark_user_no 
                                      and z.end_reason is null)
                     group by a.p_trademark_reg_no
                """
                p_rows = await conn.fetch(p_query)
                logger.info(f"[DB] 보호 상표 {len(p_rows)}건 조회됨")
                
                for p_row in p_rows:
                    # 보호 상표 정보 매핑
                    p_tm_dict = {
                        "p_trademark_reg_no"      : p_row["p_trademark_reg_no"],
                        "p_trademark_name"        : p_row["p_trademark_name"] or "",
                        "p_trademark_type"        : p_row["p_trademark_type"] or "",
                        "p_trademark_class_code"  : p_row["p_trademark_class_code"] or "",
                        "p_trademark_image"       : self._encode_image(p_row["p_trademark_image"]),
                        "p_trademark_image_vec"   : json.loads(p_row["p_trademark_image_vec"]),
                        "p_trademark_user_no"     : p_row["p_trademark_user_no"],
                        "p_product_kinds"         : p_row["p_product_kinds"] or "",
                    }
                    
                    name_vec = p_row["p_trademark_name_vec"] 
                    image_vec = p_row["p_trademark_image_vec"]
                    
                    # 유사 수집 상표 검색
                    vector_conditions = []
                    params = []
                    param_idx = 1
                    
                    if name_vec:
                        vector_conditions.append(f"(c_trademark_name_vec <=> ${param_idx}) <= {distance_threshold}")
                        params.append(str(name_vec))
                        param_idx += 1
                    
                    if image_vec:
                        vector_conditions.append(f"(c_trademark_image_vec <=> ${param_idx}) <= {distance_threshold}")
                        params.append(str(image_vec))
                        param_idx += 1
                    
                    if not vector_conditions:
                        continue
                    
                    class_code_where = []
                    class_code_where.append(f"({' OR '.join(vector_conditions)})")
                    
                    if p_row["p_trademark_class_code"]:
                        class_code_where.append(f"string_to_array(c_trademark_class_code, '|') && string_to_array(${param_idx}, '|')")
                        params.append(p_row["p_trademark_class_code"])
                        param_idx += 1
                    
                    where_clause = " AND ".join(class_code_where)
                    
                    # 현재 보호 상표에 대해 이미 침해 위험군 테이블에 등록된 수집 상표는 제외
                    where_clause += f" AND not exists (select 1 from tbl_infringe_risk c where c.p_trademark_reg_no = ${param_idx} and c.c_product_name = a.c_product_name and (c.c_trademark_name = a.c_trademark_name or c.c_trademark_image = a.c_trademark_image))"
                    params.append(p_row["p_trademark_reg_no"])
                    param_idx += 1

                    c_query = f"""
                        SELECT c_trademark_no, 
                               c_product_name, 
                               c_product_page_url, 
                               c_manufacturer_info, 
                               c_brand_info, 
                               c_l_category, 
                               c_m_category, 
                               c_s_category,                        
                               c_trademark_type, 
                               c_trademark_class_code,                        
                               c_trademark_name, 
                               c_trademark_name_vec,
                               c_trademark_image,
                               c_trademark_image_vec,
                               c_trademark_ent_date
                        FROM tbl_collect_trademark a
                        WHERE {where_clause}
                        LIMIT 100 
                    """
                    logger.info(f"c_query: {c_query}")
                    c_rows = await conn.fetch(c_query, *params)
                    
                    c_tm_list = []
                    for c_row in c_rows:
                        c_tm_dict = {
                            "c_trademark_no"                : c_row["c_trademark_no"],
                            "c_product_name"                : c_row["c_product_name"] or "",
                            "c_product_page_url"            : c_row["c_product_page_url"] or "",
                            "c_manufacturer_info"           : c_row["c_manufacturer_info"] or "",
                            "c_brand_info"                  : c_row["c_brand_info"] or "",
                            "c_l_category"                  : c_row["c_l_category"] or "",
                            "c_m_category"                  : c_row["c_m_category"] or "",
                            "c_s_category"                  : c_row["c_s_category"] or "",
                            "c_trademark_type"              : c_row["c_trademark_type"] or "",
                            "c_trademark_class_code"        : c_row["c_trademark_class_code"] or "",
                            "c_trademark_name"              : c_row["c_trademark_name"] or "",
                            "c_trademark_name_vec"          : json.loads(c_row["c_trademark_name_vec"]) if c_row["c_trademark_name_vec"] is not None else [],
                            "c_trademark_image"             : self._encode_image(c_row["c_trademark_image"]),
                            "c_trademark_image_vec"         : json.loads(c_row["c_trademark_image_vec"]) if c_row["c_trademark_image_vec"] is not None else [],
                            "c_trademark_ent_date"          : c_row["c_trademark_ent_date"],
                        }
                        c_tm_list.append(c_tm_dict)
                    
                    if c_tm_list:
                        logger.debug(f"[DB] '{p_row['p_trademark_name']}'의 유사 상표 {len(c_tm_list)}건 발견")
                        results.append({
                            "protection_trademark": p_tm_dict,
                            "collected_trademarks": c_tm_list
                        })
            
            logger.info(f"[DB] 검색 종료: 총 {len(results)}개 그룹 발견")
            return results
        except Exception as e:
            logger.error(f"[DB] 유사 상표 검색 중 오류: {e}", exc_info=True)
            return []

    async def save_infringe_risk(self, risk_data: Dict[str, Any]):
        """침해 위험군 테이블 저장"""
        try:
            pool = await Database.get_pool()
            
            c_tm = risk_data.get("c_tm")
            ensemble_result = risk_data.get("ensemble_result")
            p_trademark_reg_no = risk_data.get("p_trademark_reg_no")

            query = """
                INSERT INTO tbl_infringe_risk (
                    c_product_name, c_product_page_url, c_manufacturer_info, c_brand_info, c_l_category, 
                    c_m_category, c_s_category, c_trademark_type, c_trademark_class_code, c_trademark_name, 
                    c_trademark_name_vec, c_trademark_image, c_trademark_image_vec, c_trademark_ent_date, visual_score, 
                    visual_weight, phonetic_score, phonetic_weight, conceptual_score, conceptual_weight,
                    total_score, risk_level, judge_date,p_trademark_reg_no
                ) VALUES (
                    $1, $2, $3, $4, $5, 
                    $6, $7, $8, $9, $10, 
                    $11, $12, $13, $14, $15, 
                    $16, $17, $18, $19, $20,
                    $21, $22, NOW(), $23
                )
            """
            
            c_image_bytes = self._decode_image(c_tm.c_trademark_image)
            
            # 빈 리스트 처리
            c_trademark_name_vec = str(c_tm.c_trademark_name_vec) if c_tm.c_trademark_name_vec else None
            c_trademark_image_vec = str(c_tm.c_trademark_image_vec) if c_tm.c_trademark_image_vec else None
            
            params = [
                c_tm.c_product_name, c_tm.c_product_page_url, c_tm.c_manufacturer_info, c_tm.c_brand_info, c_tm.c_l_category, 
                c_tm.c_m_category, c_tm.c_s_category, c_tm.c_trademark_type, c_tm.c_trademark_class_code, c_tm.c_trademark_name, 
                c_trademark_name_vec, c_image_bytes, c_trademark_image_vec, c_tm.c_trademark_ent_date, ensemble_result.visual_score, 
                ensemble_result.visual_weight, ensemble_result.phonetic_score, ensemble_result.phonetic_weight, ensemble_result.conceptual_score, ensemble_result.conceptual_weight,
                ensemble_result.total_score, ensemble_result.risk_level,p_trademark_reg_no
            ]
            
            async with pool.acquire() as conn:
                await conn.execute(query, *params)
            
            logger.info(f"[DB] 위험군 저장 성공 (상표명: {c_tm.c_trademark_name})")

        except Exception as e:
            logger.error(f"[DB] 위험군 저장 오류: {e}", exc_info=True)

    async def search_reason_trademark(self, query_vec: List[float], top_k: int) -> List[ReasonTrademark]:
        try:
            pool = await Database.get_pool()
            
            query = f"""
                SELECT 
                    patent_id, 
                    cleaned_content, 
                    reason_tags, 
                    product_tags,
                    1 - (cleaned_content_vec <=> $1) AS similarity
                FROM tbl_reason_trademark
                ORDER BY cleaned_content_vec <=> $1 ASC
                LIMIT $2
            """
            async with pool.acquire() as conn:
                rows = await conn.fetch(query, str(query_vec), top_k)
                
                results = []
                for row in rows:
                    results.append(ReasonTrademark(
                        patent_id = str(row["patent_id"]),         
                        cleaned_content = row["cleaned_content"],  
                        reason_tags = row["reason_tags"],          
                        product_tags = row["product_tags"],        
                        similarity_score = row["similarity"],      
                    ))
                return results
        except Exception as e:
            logger.error(f"[DB] 거절사유 검색 오류: {e}")
            return []

    async def search_precedent(self, query_vec: List[float], target_hml: str, l_limit: int, f_limit: int) :
        """단수형 메서드: HML 패턴 매칭 포함 판례 검색"""
        try:
            pool = await Database.get_pool()
            
            query = f"""
                (SELECT precedent_no, case_id, content, chunk_index, topic, hml_pattern, file_name, start_page, ruling_history,
                        (1 - (content_vec <=> CAST($1 AS public.vector))) * (CASE WHEN hml_pattern = $2 THEN 1.2 ELSE 1.0 END) as score
                    FROM tbl_precedent WHERE topic = '법리' 
                    ORDER BY score DESC LIMIT $3)
                UNION ALL
                (SELECT precedent_no, case_id, content, chunk_index, topic, hml_pattern, file_name, start_page, ruling_history,
                        (1 - (content_vec <=> CAST($1 AS public.vector))) * (CASE WHEN hml_pattern = $2 THEN 1.2 ELSE 1.0 END) *
                        (CASE WHEN content ~ '대법원|판결|선고|제[0-9]+조' THEN 0.5 ELSE 1.0 END) as score
                    FROM tbl_precedent WHERE topic = '본문' 
                    ORDER BY score DESC LIMIT $4)
            """
            
            async with pool.acquire() as conn:
                rows = await conn.fetch(query, str(query_vec), target_hml, l_limit, f_limit)
                
                results = []
                
                for r in rows:
                    results.append({**dict(r), "unique_key": f"{r['case_id']}_{r['chunk_index']}"})
                    
                return results
        except Exception as e:
            logger.error(f"[DB] 판례 검색 오류: {e}")
            return []

    def _encode_image(self, image_bytes: Optional[bytes]) -> Optional[str]:
        """Bytes -> Base64 String"""
        try:
            if image_bytes is None:
                return None
            return base64.b64encode(image_bytes).decode('utf-8')
        except Exception as e:
            logger.error(f"이미지 인코딩 오류: {e}")
            return None

    def _decode_image(self, image_str: Optional[str]) -> Optional[bytes]:
        """Base64 String -> Bytes"""
        try:
            if image_str is None:
                return None
            return base64.b64decode(image_str)
        except Exception as e:
            logger.error(f"이미지 디코딩 오류: {e}")
            return None

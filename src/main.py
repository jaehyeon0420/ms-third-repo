import asyncio
import os
from dotenv import load_dotenv
from src.graph.state import GraphState
from src.utils.db import Database
from src.container import Container
from src.graph.workflow import app
from src.utils.logger import get_logger
from src.services.send_mail import send_report_mail
from src.model.schema import ApprovedReport, ProtectionTrademarkInfo, CollectedTrademarkInfo

logger = get_logger(__name__)

async def main():
    """
    TIP 프로젝트 메인 실행 스크립트 (Azure Container Job 진입점)
    1. DB 연결
    2. 보호 상표 및 유사 수집 상표 조회 (Batch Fetch)
    3. 각 상표 쌍에 대해 LangGraph 워크플로우 실행
    4. 결과 처리 및 종료
    """
    
    # DB 연결 초기화
    logger.info("데이터베이스 연결 초기화 중...")
    await Database.get_pool()
    vector_store = Container.get_vector_store()
    total_processed = 0
    
    try:
        logger.info("TIP 배치 작업 시작...")
        
        # 보호 상표 및 수집 상표 정보 조회
        target_groups = await vector_store.search_similar_trademarks()
        
        if not target_groups:
            logger.info("처리할 유사 상표가 없습니다.")
            return

        logger.info(f"유사 상표 후보가 있는 보호 상표 {len(target_groups)}개를 찾았습니다.")

        # 배치 루프 실행
        
        for group in target_groups:
            p_tm = group["protection_trademark"]        # 보호 상표 1개 정보
            c_tm_list = group["collected_trademarks"]   # 수집 상표 N개 정보
            
            # 수집 상표가 존재하지 않으면 Skip
            if not c_tm_list:
                continue
            
            # Pydantic 모델 변환
            try:
                p_tm = ProtectionTrademarkInfo(**p_tm)
                c_tm_list = [CollectedTrademarkInfo(**ct) for ct in c_tm_list]
            except Exception as e:
                logger.error(f"데이터 유효성 검사 오류 (상표명: {p_tm.p_trademark_name}): {e}")
                continue
                
            logger.info(f"보호 상표 처리 중: {p_tm.p_trademark_name} (ID: {p_tm.p_trademark_user_no})")
            logger.info(f" - 발견된 후보 상표 수: {len(c_tm_list)}개")
            
            # 보고서 누적 리스트 초기화 (보호 상표 단위)
            approved_reports: list[ApprovedReport] = []
            
            # 수집 상표 N개를 하나씩 순회하며 Graph 실행 (1:1 비교 컨텍스트)
            for c_tm in c_tm_list:
                
                # 수집 상표명
                c_tm_name = c_tm.c_trademark_name
                logger.info(f"  후보 상표 분석 시작: {c_tm_name}")
                
                # LangGraph State 구성
                initial_state: GraphState = {
                    "protection_trademark": p_tm,
                    "collected_trademarks": c_tm_list,
                    "current_collected_trademark": c_tm,
                    "visual_similarity_score": 0.0,
                    "visual_weight": 0.0,
                    "phonetic_similarity_score": 0.0,
                    "phonetic_weight": 0.0,
                    "conceptual_similarity_score": 0.0,
                    "conceptual_weight": 0.0,
                    "conceptual_description": "",
                    "ensemble_result": None,
                    "search_querys": [],
                    "retrieved_precedents": [],
                    "refined_precedents": [],
                    "grading_decision": "",
                    "query_feedback": "",
                    "web_search_keywords": [],
                    "is_precedent_exists": False,
                    "report_content": "",
                    "evaluation_score": 0.0,
                    "evaluation_feedback": "",
                    "evaluation_decision": "",
                    "rewrite_count": 0,
                    "web_search_count": 0,
                    "regeneration_count": 0,
                    "is_infringement_found": False
                }
                
                # Graph 비동기 실행
                try:
                    result = await app.ainvoke(initial_state)
                    
                    is_infringement = result.get("is_infringement_found", False)
                    ensemble_result = result.get("ensemble_result")
                    risk_level = ensemble_result.risk_level if ensemble_result else "N/A"
                    
                    status_icon = "🚨" if is_infringement else "✅"
                    logger.info(f"  {status_icon} [{c_tm_name}] 분석 결과: 침해여부={is_infringement}, 위험등급={risk_level}")
                    
                    # 보고서 승인 시 리스트에 누적 (메일 발송은 루프 종료 후)
                    evaluation_decision = result.get("evaluation_decision", "")
                    if evaluation_decision == "approved":
                        c_tm_info = result.get("current_collected_trademark")
                        
                        approved_reports.append(ApprovedReport(
                            c_trademark_name=c_tm_info.c_trademark_name,
                            c_trademark_image=c_tm_info.c_trademark_image,
                            report_content=result.get("report_content", ""),
                            risk_level=risk_level,
                            total_score=ensemble_result.total_score if ensemble_result else 0.0
                        ))
                    
                    total_processed += 1
                    
                except Exception as e:
                    logger.error(f"      ❌ {c_tm_name} 처리 중 오류 발생: {e}", exc_info=True)
            
            # 수집 상표 N개 처리 완료 후, 승인된 보고서가 있으면 메일 발송
            if approved_reports:
                logger.info(f"  {p_tm.p_trademark_name}에 대한 보고서 {len(approved_reports)}건 메일 발송 중...")
                
                try:
                    await send_report_mail(
                        approved_reports=approved_reports,
                        p_trademark_reg_no=p_tm.p_trademark_reg_no,
                        p_trademark_name=p_tm.p_trademark_name,
                        p_trademark_image=p_tm.p_trademark_image,
                    )
                except Exception as e:
                    logger.error(f"   ❌ 메일 발송 중 오류 발생: {e}", exc_info=True)

    except Exception as e:
        logger.error(f"❌ 배치 작업 중 치명적인 오류 발생: {e}", exc_info=True)
        raise e
        
    finally:
        # 6. 리소스 정리
        await Database.close()
        logger.info(f"🏁 작업 종료. 총 처리 건수: {total_processed}")

if __name__ == "__main__":
    asyncio.run(main())

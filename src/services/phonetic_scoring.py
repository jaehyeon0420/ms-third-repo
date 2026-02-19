from src.utils.format import clean_hangul, apply_korean_phonetics
from src.utils.llm import generate_text
from src.container import Container
from src.configs import get_system_prompt
from src.utils.logger import get_logger
from rapidfuzz import fuzz, distance
from jamo import h2j, j2hcj
import re
import json


logger = get_logger(__name__)

def calculate_phonetic_similarity(p_trademark_name: str, c_trademark_name: str) -> float:
    """Model B: 호칭 유사도"""
    try:
        # 상표명 한글 음역 표준화 작업
        logger.info(f"[호칭 유사도] 음역 표준화 요청: {p_trademark_name}, {c_trademark_name}")
        list_a = _convert_pair(p_trademark_name)
        list_b = _convert_pair(c_trademark_name)
        
        logger.info(f"[호칭 유사도] 음역 결과: A={list_a}, B={list_b}")

        # 각 발음 리스트에서 가장 긴 발음의 80% 이상 길이만 유효한 비교 대상으로 삼음 (지에스 vs 지에스이시보 방지)
        max_len_a = max(len(p) for p in list_a) if list_a else 0
        max_len_b = max(len(p) for p in list_b) if list_b else 0
        valid_a = [p for p in list_a if len(p) >= max_len_a * 0.8]
        valid_b = [p for p in list_b if len(p) >= max_len_b * 0.8]
        
        if not valid_a or not valid_b:
            logger.warning("[호칭 유사도] 유효한 발음 후보가 없어 점수 0점 처리")
            return 0.0

        best = {"score": -1.0, "p_a": "", "p_b": "", "case": "", "grade": ""}
        all_combos = []

        for p_a in valid_a:
            for p_b in valid_b:
                score, grade, case = _calculate_similarity(p_a, p_b)
                all_combos.append((p_a, p_b, score, case))
                if score > best["score"]:
                    best.update({
                        "score": score, "p_a": p_a, "p_b": p_b, 
                        "case": case, "grade": grade
                    })
            
        logger.info(f"[호칭 유사도] 최고 매칭: {best['p_a']} vs {best['p_b']} -> 점수: {best['score']:.2f} ({best['case']})")
        return round(best["score"], 2)
    except Exception as e:
        logger.error(f"[호칭 유사도] 계산 중 오류: {e}", exc_info=True)
        return 0.0

            
def _convert_pair(trademark_name):   
    try:
        trademark_name = trademark_name.strip()
                
        model = Container.get_gpt51_chat()
        
        response = generate_text(model, get_system_prompt("phonetic_similarity"), 
                                        f"Brand : {trademark_name}", 
                                        "")
        
        parsed = None
        clean = re.sub(r'```json\s*|```\s*', '', response)
        start, end = clean.find('{'), clean.rfind('}')
        
        if start != -1 and end != -1:
            json_str = clean[start:end+1]
            try:
                parsed = json.loads(json_str)
            except json.JSONDecodeError:
                try:
                    json_str_fixed = json_str.replace('\\"', '"').replace("'", '"')
                    parsed = json.loads(json_str_fixed)
                except :
                    pass
        
        if parsed:
            # [Fix 2026-02-19] GPT가 {"korean": [], "korean_b": ["라온"]} 처럼 빈 리스트를 먼저 줄 경우 대비
            # 유효한(값이 있는) 리스트를 우선 찾고, 없으면 마지막 리스트라도 반환
            valid_result = None
            
            for key in parsed:
                if isinstance(parsed[key], list):
                    k_list = clean_hangul(parsed[key])
                    if any(k_list) and k_list != [""]:
                        valid_result = k_list
                        break # 유효한 값을 찾았으면 즉시 반환
            
            if valid_result:
                return apply_korean_phonetics(valid_result)
            
            # 만약 유효한 값이 없으면 첫 번째 리스트라도 사용 (혹은 원본)
            for key in parsed:
                if isinstance(parsed[key], list):
                    k_list = clean_hangul(parsed[key])
                    return apply_korean_phonetics(k_list)
        
        logger.warning(f"[호칭 유사도] JSON 파싱 실패 (원본 사용): {response[:50]}...")
              
    except Exception as e:
        logger.error(f"[호칭 유사도] 음역 변환 중 오류: {e}")

    return apply_korean_phonetics([trademark_name])

def _calculate_similarity(pron_a, pron_b):
    """ 3-Tier Decision Logic 기반 최종 유사도 산출"""
    try:
        if not pron_a or not pron_b:
            return 0.0, "Low", "Error"

        a = str(pron_a).replace(" ", "")
        b = str(pron_b).replace(" ", "")
        
        len_a, len_b = len(a), len(b)
        if len_a == 0 or len_b == 0:
            return 0.0, "Low", "N/A"
        
        longer = max(len_a, len_b)
        ratio = min(len_a, len_b) / longer

        # 알고리즘 3대장 점수 산출
        jamo_score = _calculate_custom_jamo_score(a, b)
        jw_score = distance.JaroWinkler.similarity(a, b) * 100
        partial_score = fuzz.partial_ratio(a, b)

        final_score = 0.0
        case_name = ""

        # Case 1: Microscope (짧은 단어) -> Jamo 우선
        if longer <= 3 and ratio >= 0.7:
            case_name = "Case 1"
            final_score = (jamo_score * 0.5) + (jw_score * 0.3) + (partial_score * 0.2)

        # Case 2: Telescope (긴 단어) -> JW 우선 (어두 강조)
        elif longer > 3 and ratio >= 0.7:
            case_name = "Case 2"
            final_score = (jw_score * 0.5) + (jamo_score * 0.3) + (partial_score * 0.2)

        # Case 3: Inclusion (길이 차이 큼) -> Partial 우선
        else:
            case_name = "Case 3"
            final_score = (partial_score * 0.7) + (jamo_score * 0.2) + (jw_score * 0.1)

        grade = "High" if final_score >= 80 else "Medium" if final_score >= 50 else "Low"
        return final_score, grade, case_name

    except Exception as e:
        logger.error(f"[호칭 유사도] 유사도 계산 로직 오류: {e}")
        return 0.0, "Error", str(e)


def _calculate_custom_jamo_score(pron_a, pron_b):
    """
    한국어 음운 특성(ㄹ 받침, 까/카 유사성)을 반영한 세밀 유사도 계산
    """
    try:
        # h2j를 통한 음절 분해
        j_a = h2j(pron_a)
        j_b = h2j(pron_b)
        
        # 음절 수가 다를 경우 기본 자모 유사도로 전환
        if len(j_a) != len(j_b):
            return fuzz.ratio(j2hcj(j_a), j2hcj(j_b))
        
        total_jamo_score = 0
        total_elements = 0
        
        for char_a, char_b in zip(j_a, j_b):
            # IndexError 방지를 위한 안전장치: 초성+중성 조합 확인
            if len(char_a) < 2 or len(char_b) < 2:
                total_elements += 1
                total_jamo_score += 1.0 if char_a == char_b else 0.0
                continue

            # 음절 분해: [초성, 중성, 종성]
            s_a = [char_a[0], char_a[1], char_a[2] if len(char_a) > 2 else ""]
            s_b = [char_b[0], char_b[1], char_b[2] if len(char_b) > 2 else ""]
            
            for i in range(3):
                total_elements += 1
                val_a, val_b = s_a[i], s_b[i]
                
                if val_a == val_b:
                    total_jamo_score += 1.0
                else:
                    # 지침 반영: 초성 'ㄲ'과 'ㅋ'의 청감적 유사성 (96%)
                    if i == 0 and {val_a, val_b} == {'ㄲ', 'ㅋ'}:
                        total_jamo_score += 0.96
                    
                    # 지침 반영: 종성 'ㄹ'의 유무 차이 최소화 (98%)
                    elif i == 2:
                        if (not val_a and val_b == 'ㄹ') or (val_a == 'ㄹ' and not val_b):
                            total_jamo_score += 0.98
        
        return (total_jamo_score / total_elements) * 100 if total_elements > 0 else 0.0
    except Exception as e:
        logger.error(f"[호칭 유사도] 자모 계산 오류: {e}")
        return 0.0

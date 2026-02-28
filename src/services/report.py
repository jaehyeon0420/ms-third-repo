from src.configs import get_system_prompt, render_user_prompt, model_config
from src.container import Container
from src.utils.format import clean_qwen_response
from src.utils.llm import generate_text
from src.model.schema import EvaluationResult
from langchain_core.messages import SystemMessage, HumanMessage


def generate_report(context: dict) -> str:
    system_prompt = get_system_prompt("report_generation")
    user_prompt = render_user_prompt("report_generation", **context)
    
    # 모델 파라미터 로드
    qwen_config = model_config.get('models', {}).get('qwen_reporter', {})
    
    # Container에서 vLLM 클라이언트 가져오기
    aclient = Container.get_vllm_client()
    
    response = aclient.chat.completions.create(
        model="trademark-analysis",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        temperature=qwen_config.get('temperature'),
        max_tokens=qwen_config.get('max_tokens'),
        top_p=qwen_config.get('top_p'),
        presence_penalty=1.0
    )
    
    raw_content = response.choices[0].message.content
    cleaned_content = clean_qwen_response(raw_content)
    
    return cleaned_content
    
# def generate_report(context: dict) -> str:
#     model = Container.get_gpt51_chat()
    
#     system_prompt = get_system_prompt("report_generation")
#     user_prompt = render_user_prompt("report_generation", **context)
        
#     report_content = generate_text(model, system_prompt, user_prompt, "")
    
#     return report_content


def evaluate_report(context: dict, report_content: str) -> EvaluationResult:
    base_context = render_user_prompt("precedent_grading", **context)
        
    # 평가용 프롬프트 렌더링
    eval_context = render_user_prompt("report_evaluation", 
        base_context=base_context, 
        report_content=report_content
    )
    
    # Container에서 LLM 가져오기
    llm_judge = Container.get_gpt51_chat()
    
    # EvaluationResult 형식에 맞게 구조화된 출력    
    structured_llm = llm_judge.with_structured_output(EvaluationResult)
    
    eval_system_prompt = get_system_prompt("report_evaluation")
    
    result: EvaluationResult = structured_llm.invoke([
        SystemMessage(content=eval_system_prompt),
        HumanMessage(content=eval_context)
    ])
    
    return result
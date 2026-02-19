import os
import smtplib
import base64
from email.mime.image import MIMEImage
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from src.utils.db import Database
from src.utils.logger import get_logger
from src.model.schema import ApprovedReport

logger = get_logger(__name__)


async def _get_agent_email(p_trademark_reg_no: str) -> str | None:
    """
    보호 상표 번호로 담당 변리사 이메일을 조회
    """
    pool = await Database.get_pool()
    
    query = """
        SELECT c.agent_email
          FROM tbl_protection_trademark a,
               tbl_customer_info b,
               tbl_patent_attorney c
         WHERE a.p_trademark_reg_no = $1
           AND a.p_trademark_user_no = b.customer_no
           AND c.agent_id = b.agent_id
    """
    
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(query, p_trademark_reg_no)
            
        if not row:
            logger.warning(f"[이메일] 담당 변리사 이메일을 찾을 수 없음 (등록번호: {p_trademark_reg_no})")
            return None
            
        return row["agent_email"]
        
    except Exception as e:
        logger.error(f"[이메일] DB 조회 오류: {e}")
        return None


def _build_email_body(approved_reports: list[ApprovedReport], p_trademark_name: str, p_trademark_image: str) -> str:
    """보호 상표 1건에 대한 승인된 보고서 N건을 하나의 메일 본문으로 구성"""
    try:
        
                
        reports_html = ""
        for idx, report in enumerate(approved_reports, 1):
            collect_image_data = _get_base64_image(report.c_trademark_image)
            
            # img 태그 생성 (데이터 타입이 png라면 image/png로 설정)
            collect_image_tag = f"""
            <div style="margin-top: 15px; text-align: left;">
                <img src="data:image/jpeg;base64,{collect_image_data}" 
                        alt="{report.c_trademark_name} 이미지" 
                        style="max-width: 100%; height: auto; border: 1px solid #eee;"/>
            </div>
            """
            formatted_content = report.report_content.replace("\n", "<br>")
            reports_html += f"""
            <div style="margin-bottom: 30px; border: 1px solid #ddd; padding: 15px; border-radius: 5px;">
                <h3>#{idx}. 침해 의심 상표: {report.c_trademark_name}</h3>
                <p>위험도: 
                    <strong style="color: {report.risk_level=="H" and "red" or report.risk_level=="M" and "orange" or "green"};">{report.risk_level=="H" and "고위험" or report.risk_level=="M" and "중위험" or "저위험"}</strong>
                    | 종합 점수: <strong>{report.total_score * 100:.2f}점</strong></p>
                {collect_image_tag}
                <hr/>
                <div style="background-color: #f9f9f9; padding: 10px; font-family: monospace;">
                    {formatted_content}
                </div>
            </div>
            """
        
        protection_image_data = _get_base64_image(p_trademark_image)
        # img 태그 생성 (데이터 타입이 png라면 image/png로 설정)
        protection_image_tag = f"""
        <div style="margin-top: 15px; text-align: center;">
            <img src="data:image/jpeg;base64,{protection_image_data}" 
                    alt="보호 상표 이미지" 
                    style="max-width: 100%; height: auto; border: 1px solid #eee;"/>
        </div>
        """
                
        return f"""
        <html>
        <body>
            <h2>[TIP] 상표권 침해 분석 보고서</h2>
            <p style="color: red;">※ 본 콘텐츠는 AI에 의해 생성되었으며, 정보의 정확성이나 완전성을 100% 보장하지 않습니다. 침해 여부 판단 시에는 반드시 사실 관계를 별도로 확인하시기 바랍니다.</p>
            <br>
            <p>안녕하세요.</p>
            <p>보호 상표 <strong>[{p_trademark_name}]</strong>에 대한 
            침해 의심 상표 <strong>{len(approved_reports)}건</strong>의 분석이 완료되었습니다.</p>
            {protection_image_tag}
            <hr/>
            {reports_html}
        </body>
        </html>
        """
    except Exception as e:
        logger.error(f"[이메일] 본문 생성 중 오류: {e}")
        return "<p>본문 생성 실패</p>"


async def send_report_mail(
    approved_reports: list[ApprovedReport],
    p_trademark_reg_no: str,
    p_trademark_name: str,
    p_trademark_image: str,
) -> bool:
    """
    보호 상표 1건에 대한 승인된 보고서 N건을 담당 변리사에게 메일로 일괄 발송
    """
    try:
        if not approved_reports:
            logger.info("[이메일] 발송할 보고서가 없어 중단")
            return False
            
        agent_email = await _get_agent_email(p_trademark_reg_no)
        
        if not agent_email:
            logger.warning(f"[이메일] 수신자 이메일 없음. 발송 취소. ({p_trademark_name})")
            return False

        # 2. 메일 구성
        smtp_server = os.getenv("SMTP_SERVER")
        smtp_port_env = os.getenv("SMTP_PORT", "587")
        smtp_port = int(smtp_port_env) if smtp_port_env.isdigit() else 587
        smtp_user = os.getenv("SMTP_USER")
        smtp_password = os.getenv("SMTP_PASSWORD")
        
        if not all([smtp_server, smtp_user, smtp_password]):
            logger.error("[이메일] SMTP 환경변수 설정 누락 (.env 확인 필요)")
            return False
        
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"[TIP] 상표권 침해 분석 보고서 - {p_trademark_name} ({len(approved_reports)}건)"
        msg["From"] = smtp_user
        msg["To"] = agent_email
        
        body = _build_email_body(approved_reports, p_trademark_name, p_trademark_image)
        msg.attach(MIMEText(body, "html"))
        
        
        # 보호 상표
        if p_trademark_image is not None:
                # MIMEImage 객체 생성 (이미지 데이터가 bytes 형태이므로 바로 전달 가능)
                # 이미지 타입이 jpg가 아니라면 _subtype을 'png' 등으로 지정 가능
                image_data = _get_image_bytes(p_trademark_image)
                img_part = MIMEImage(image_data, _subtype="jpeg")
                
                # 첨부파일 이름 설정 (수신자가 보게 될 파일명)
                filename = f"[보호 대상 상표]_{p_trademark_name}.jpg"
                img_part.add_header('Content-Disposition', 'attachment', filename=filename)
                
                # 메일에 첨부
                msg.attach(img_part)
        # 수집 상표        
        for idx, report in enumerate(approved_reports, 1):
            if report.c_trademark_image is not None:
                # MIMEImage 객체 생성 (이미지 데이터가 bytes 형태이므로 바로 전달 가능)
                # 이미지 타입이 jpg가 아니라면 _subtype을 'png' 등으로 지정 가능
                image_data = _get_image_bytes(report.c_trademark_image)
                img_part = MIMEImage(image_data, _subtype="jpeg")
                
                # 첨부파일 이름 설정 (수신자가 보게 될 파일명)
                filename = f"[침해 의심 상표]_[{idx}]_{report.c_trademark_name}.jpg"
                img_part.add_header('Content-Disposition', 'attachment', filename=filename)
                
                # 메일에 첨부
                msg.attach(img_part)
        
        # 3. 메일 발송
        logger.info(f"[이메일] SMTP 서버 연결 시도: {smtp_server}:{smtp_port}")
        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()
            server.login(smtp_user, smtp_password)
            server.sendmail(smtp_user, agent_email, msg.as_string())
            
        logger.info(f"[이메일] 발송 성공: {agent_email} (건수: {len(approved_reports)})")
        return True
        
    except Exception as e:
        logger.error(f"[이메일] 발송 실패: {e}", exc_info=True)
        return False


# 본문용 이미지 변환
def _get_base64_image(image_data: str) -> str:
    try:
        # 1. 만약 이미 바이트 타입이라면 그대로 인코딩
        if isinstance(image_data, bytes):
            return base64.b64encode(image_data).decode('utf-8')
        
        # 2. 문자열(str) 타입인 경우 처리
        if isinstance(image_data, str):
            # 케이스 A: PostgreSQL Hex 방식 (\x로 시작하는 경우)
            if image_data.startswith('\\x'):
                hex_data = image_data[2:] # \x 제거
                return base64.b64encode(bytes.fromhex(hex_data)).decode('utf-8')
            
            # 케이스 B: 이미 Base64 문자열인 경우 (그대로 반환)
            # (간단한 체크: 알파벳/숫자로만 구성되어 있고 길이가 충분히 길다면)
            if len(image_data) > 100 and ',' not in image_data: 
                return image_data
            
            # 케이스 C: 일반 문자열인데 바이트로 변환이 필요한 경우
            return base64.b64encode(image_data.encode('utf-8')).decode('utf-8')
            
    except Exception as e:
        print(f"이미지 변환 오류: {e}")
        return None
    
# 첨부파일용 이미지 바이트 변환    
def _get_image_bytes(image_data) -> bytes:
    """DB에서 가져온 데이터를 순수 바이트(bytes)로 변환 (첨부파일용)"""
    try:
        if image_data is None:
            return None
            
        # 1. 이미 bytes 타입인 경우 (가장 이상적)
        if isinstance(image_data, bytes):
            return image_data
        
        # 2. 문자열(str) 타입인 경우 (PostgreSQL bytea를 str로 조회했을 때)
        if isinstance(image_data, str):
            # 케이스: PostgreSQL Hex 방식 (\x로 시작)
            if image_data.startswith('\\x'):
                hex_data = image_data[2:] # \x 제거
                return bytes.fromhex(hex_data) # 16진수 문자열을 다시 바이트로!
            
            # 케이스: 만약 실수로 이미 Base64로 인코딩된 문자열이 들어왔다면 디코딩
            try:
                return base64.b64decode(image_data)
            except:
                pass
                
        return None
    except Exception as e:
        print(f"이미지 바이트 변환 오류: {e}")
        return None    
import os
import re
import shutil
from pathlib import Path
from .net import post_json
from .settings import ROOT

def publish_repo_pdf(path, day, config, root=ROOT):
    """Copy the PDF into the repo's public/ folder; the automation commits it and the link is permanent."""
    base = config.get('repo_pdf_base_url') or ''
    if not base:
        raise RuntimeError('未配置仓库 PDF 基础链接（config 或 NEWSSTUDY_REPO_PDF_BASE_URL）')
    dest = Path(root) / 'public' / 'pdf' / f'{day}.pdf'
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix('.pdf.tmp')
    shutil.copyfile(path, tmp)
    os.replace(tmp, dest)
    return base.rstrip('/') + f'/public/pdf/{day}.pdf'

def upload_pdf(path, day, config, secrets):
    if not all((config['cos_region'], config['cos_bucket'], secrets.get('cos_secret_id'), secrets.get('cos_secret_key'))):
        raise RuntimeError('腾讯云 COS 配置不完整，PDF 已保留在本地')
    try:
        from qcloud_cos import CosConfig, CosS3Client
    except ImportError:
        raise RuntimeError('缺少 COS SDK，请运行安装依赖脚本') from None
    client = CosS3Client(CosConfig(Region=config['cos_region'], SecretId=secrets['cos_secret_id'],
                                  SecretKey=secrets['cos_secret_key'], Scheme='https'))
    key = f'newsstudy/{day}/report.pdf'
    try:
        client.upload_file(Bucket=config['cos_bucket'], LocalFilePath=str(path), Key=key,
                           ACL='private', ContentType='application/pdf',
                           ContentDisposition=f'attachment; filename="newsstudy-{day}.pdf"')
        url = client.get_presigned_download_url(Bucket=config['cos_bucket'], Key=key,
                                                Expired=int(config['link_hours'])*3600)
    except Exception:
        raise RuntimeError('COS 上传或生成链接失败，请检查区域、桶名、权限和网络；本地PDF不会丢失') from None
    return url

def send_wechat(title, body, sendkey):
    if re.fullmatch(r'SCT[A-Za-z0-9]+', sendkey or ''):
        url = f'https://sctapi.ftqq.com/{sendkey}.send'
    elif re.fullmatch(r'sctp\d+t[A-Za-z0-9]+', sendkey or ''):
        uid = re.match(r'sctp(\d+)t', sendkey).group(1)
        url = f'https://{uid}.push.ft07.com/send/{sendkey}.send'
    else:
        raise ValueError('SendKey 格式不正确，请从 Server酱控制台复制')
    response = post_json(url, {'title': title, 'desp': body}, timeout=40)
    if response.get('code') != 0:
        raise RuntimeError('微信推送服务未确认成功，请在服务控制台检查；不会自动重复发送')
    return True

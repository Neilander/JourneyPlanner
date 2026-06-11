"""
更换企业微信客服账号头像
用法: python update_kf_avatar.py <图片路径> [open_kfid]
      open_kfid 不传则自动列出所有客服账号供选择
"""
import os, sys, requests
from dotenv import load_dotenv

load_dotenv()
CORP_ID    = os.environ["WECOM_CORP_ID"]
KF_SECRET  = os.environ["WECOM_KF_SECRET"]

def get_token():
    r = requests.get("https://qyapi.weixin.qq.com/cgi-bin/gettoken",
                     params={"corpid": CORP_ID, "corpsecret": KF_SECRET}).json()
    token = r.get("access_token", "")
    if not token:
        sys.exit(f"获取 access_token 失败: {r}")
    return token

def list_kf_accounts(token):
    r = requests.get("https://qyapi.weixin.qq.com/cgi-bin/kf/account/list",
                     params={"access_token": token}).json()
    if r.get("errcode", 0) != 0:
        sys.exit(f"获取客服列表失败: {r}")
    return r.get("account_list", [])

def upload_image(token, img_path):
    with open(img_path, "rb") as f:
        r = requests.post(
            "https://qyapi.weixin.qq.com/cgi-bin/media/upload",
            params={"access_token": token, "type": "image"},
            files={"media": (os.path.basename(img_path), f, "image/png")},
        ).json()
    if r.get("errcode", 0) != 0:
        sys.exit(f"上传图片失败: {r}")
    return r["media_id"]

def update_kf(token, open_kfid, media_id):
    r = requests.post(
        "https://qyapi.weixin.qq.com/cgi-bin/kf/account/update",
        params={"access_token": token},
        json={"open_kfid": open_kfid, "media_id": media_id},
    ).json()
    if r.get("errcode", 0) != 0:
        sys.exit(f"更新头像失败: {r}")
    print("头像更新成功！")

def main():
    if len(sys.argv) < 2:
        sys.exit("用法: python update_kf_avatar.py <图片路径> [open_kfid]")

    img_path = sys.argv[1]
    if not os.path.exists(img_path):
        sys.exit(f"图片不存在: {img_path}")

    token = get_token()

    if len(sys.argv) >= 3:
        open_kfid = sys.argv[2]
    else:
        accounts = list_kf_accounts(token)
        if not accounts:
            sys.exit("没有找到客服账号")
        print("客服账号列表:")
        for i, acc in enumerate(accounts):
            print(f"  [{i}] {acc['name']}  open_kfid={acc['open_kfid']}")
        idx = int(input("选择序号: "))
        open_kfid = accounts[idx]["open_kfid"]

    print(f"上传图片: {img_path}")
    media_id = upload_image(token, img_path)
    print(f"media_id: {media_id}")

    update_kf(token, open_kfid, media_id)

if __name__ == "__main__":
    main()

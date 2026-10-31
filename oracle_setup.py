"""
Oracle Cloud 自動建立 ARM VM 腳本
- 沿用既有 VCN、子網路、Internet Gateway
- 產生 SSH 金鑰
- 持續嘗試建立 ARM VM（1 OCPU / 6 GB × 3 台），直到成功為止
"""

import fcntl
import hashlib
import os
import subprocess
import time
import urllib.parse
import urllib.request
from datetime import datetime

import oci

# ─── 設定 ────────────────────────────────────────────────────────
COMPARTMENT_ID = "ocid1.tenancy.oc1..aaaaaaaa33in6usn64ykyh2ji34rqmdxx3tpgk5xbugjptrn5jzhn3iw2vdq"
AVAILABILITY_DOMAIN = "BULW:AP-OSAKA-1-AD-1"
IMAGE_ID = "ocid1.image.oc1.ap-osaka-1.aaaaaaaauk2tyjjxtmfuymyrhiyxxentmj3ykig3zn6zso3266tp6afkxzwa"  # Ubuntu 24.04 aarch64
SHAPE = "VM.Standard.A1.Flex"
OCPUS = 1
MEMORY_IN_GBS = 6
BOOT_VOLUME_GB = 50  # 實測 API 下限 50 GB（映像 46.6 GB 是下限的由來，但 API 只收 >= 50）
TARGET_COUNT = 3  # 3 台 → 算力 3/4 OCPU、磁碟 50×3 + 47 = 197/200 GB
INSTANCE_PREFIX = "line-bot-a1"
SSH_KEY_PATH = os.path.expanduser("~/.ssh/oracle_line_bot")
RETRY_INTERVAL = 150  # 每 2 分半重試一次
INTER_REQUEST_DELAY = 20  # 實測連發 launch_instance 會撞 429，台與台之間隔開
LAUNCHD_LABEL = "com.steven.oracle-a1-grab"
LOG_DIR = os.path.expanduser("~/CCProject/logs")
LOCK_PATH = f"{LOG_DIR}/.oracle_a1_grab.lock"  # 防止 LaunchAgent 與手動執行併發，建出重複 VM
RESULT_PATH = f"{LOG_DIR}/oracle_a1_result.txt"  # 完成後寫入；存在即代表已搶完，刪掉可重搶


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")


def generate_ssh_key():
    """產生 SSH 金鑰（如果還沒有）"""
    if not os.path.exists(SSH_KEY_PATH):
        log("產生 SSH 金鑰...")
        subprocess.run([
            "ssh-keygen", "-t", "rsa", "-b", "4096",
            "-f", SSH_KEY_PATH, "-N", "", "-C", "oracle-line-bot"
        ], check=True)
        log(f"SSH 金鑰已產生：{SSH_KEY_PATH}")
    with open(f"{SSH_KEY_PATH}.pub") as f:
        return f.read().strip()


def setup_network(network_client):
    """建立 VCN、子網路、Internet Gateway（如果還沒有）"""

    # 檢查是否已有 VCN
    vcns = network_client.list_vcns(COMPARTMENT_ID).data
    if vcns:
        vcn = vcns[0]
        log(f"使用現有 VCN：{vcn.id}")
    else:
        log("建立 VCN...")
        vcn = network_client.create_vcn(oci.core.models.CreateVcnDetails(
            compartment_id=COMPARTMENT_ID,
            display_name="line-bot-vcn",
            cidr_block="10.0.0.0/16"
        )).data
        log(f"VCN 已建立：{vcn.id}")

    # 建立 Internet Gateway
    igws = network_client.list_internet_gateways(COMPARTMENT_ID, vcn_id=vcn.id).data
    if igws:
        igw = igws[0]
    else:
        log("建立 Internet Gateway...")
        igw = network_client.create_internet_gateway(oci.core.models.CreateInternetGatewayDetails(
            compartment_id=COMPARTMENT_ID,
            vcn_id=vcn.id,
            display_name="line-bot-igw",
            is_enabled=True
        )).data

    # 更新路由表
    rt_list = network_client.list_route_tables(COMPARTMENT_ID, vcn_id=vcn.id).data
    if rt_list:
        rt = rt_list[0]
        network_client.update_route_table(rt.id, oci.core.models.UpdateRouteTableDetails(
            route_rules=[oci.core.models.RouteRule(
                destination="0.0.0.0/0",
                network_entity_id=igw.id,
                destination_type="CIDR_BLOCK"
            )]
        ))

    # 更新安全規則（允許 SSH 和 HTTP/HTTPS）
    sl_list = network_client.list_security_lists(COMPARTMENT_ID, vcn_id=vcn.id).data
    if sl_list:
        sl = sl_list[0]
        network_client.update_security_list(sl.id, oci.core.models.UpdateSecurityListDetails(
            ingress_security_rules=[
                oci.core.models.IngressSecurityRule(
                    protocol="6", source="0.0.0.0/0",
                    tcp_options=oci.core.models.TcpOptions(
                        destination_port_range=oci.core.models.PortRange(min=22, max=22)
                    )
                ),
                oci.core.models.IngressSecurityRule(
                    protocol="6", source="0.0.0.0/0",
                    tcp_options=oci.core.models.TcpOptions(
                        destination_port_range=oci.core.models.PortRange(min=80, max=80)
                    )
                ),
                oci.core.models.IngressSecurityRule(
                    protocol="6", source="0.0.0.0/0",
                    tcp_options=oci.core.models.TcpOptions(
                        destination_port_range=oci.core.models.PortRange(min=443, max=443)
                    )
                ),
            ],
            egress_security_rules=[
                oci.core.models.EgressSecurityRule(
                    protocol="all", destination="0.0.0.0/0"
                )
            ]
        ))

    # 建立子網路
    subnets = network_client.list_subnets(COMPARTMENT_ID, vcn_id=vcn.id).data
    if subnets:
        subnet = subnets[0]
        log(f"使用現有 Subnet：{subnet.id}")
    else:
        log("建立子網路...")
        subnet = network_client.create_subnet(oci.core.models.CreateSubnetDetails(
            compartment_id=COMPARTMENT_ID,
            vcn_id=vcn.id,
            display_name="line-bot-subnet",
            cidr_block="10.0.0.0/24",
            availability_domain=AVAILABILITY_DOMAIN
        )).data
        log(f"Subnet 已建立：{subnet.id}")

    return subnet.id


def create_instance(compute_client, subnet_id, ssh_public_key, name):
    """嘗試建立一台 VM"""
    details = oci.core.models.LaunchInstanceDetails(
        compartment_id=COMPARTMENT_ID,
        availability_domain=AVAILABILITY_DOMAIN,
        display_name=name,
        shape=SHAPE,
        shape_config=oci.core.models.LaunchInstanceShapeConfigDetails(
            ocpus=OCPUS,
            memory_in_gbs=MEMORY_IN_GBS
        ),
        source_details=oci.core.models.InstanceSourceViaImageDetails(
            image_id=IMAGE_ID,
            boot_volume_size_in_gbs=BOOT_VOLUME_GB
        ),
        create_vnic_details=oci.core.models.CreateVnicDetails(
            subnet_id=subnet_id,
            assign_public_ip=True
        ),
        metadata={"ssh_authorized_keys": ssh_public_key}
    )
    return compute_client.launch_instance(
        details,
        # 同一台固定 token：逾時重試時伺服器端視為同一請求，不會重複建出第二台同名 VM
        opc_retry_token=hashlib.sha256(name.encode()).hexdigest()[:32],
    ).data


def list_ours(compute_client):
    """回傳 {名稱: instance}，只算自家的、非 TERMINATED 的 VM。

    TERMINATED 的記錄在 OCI 還會保留 12 小時，若當成「已建立」會導致
    永遠不補建、卻印出全部完成。
    """
    instances = oci.pagination.list_call_get_all_results(
        compute_client.list_instances, COMPARTMENT_ID
    ).data
    return {
        i.display_name: i
        for i in instances
        if i.display_name.startswith(INSTANCE_PREFIX)
        and i.lifecycle_state not in ("TERMINATED", "TERMINATING")
    }


def get_public_ip(compute_client, network_client, instance_id):
    """等 VNIC 拿到公開 IP，最多 60 秒；取 primary 網卡，不靠清單順序"""
    for _ in range(12):
        attachments = compute_client.list_vnic_attachments(
            COMPARTMENT_ID, instance_id=instance_id
        ).data
        # VnicAttachment 沒有 is_primary 欄位（實測 oci 2.168.2），主要網卡看 nic_index == 0
        usable = [a for a in attachments if a.vnic_id]
        primary = [a for a in usable if a.nic_index == 0] or usable
        if primary:
            vnic = network_client.get_vnic(primary[0].vnic_id).data
            if vnic.public_ip:
                return vnic.public_ip
        time.sleep(5)
    return None


def notify(text):
    """搶到後推 Telegram；token 不存在就跳過（token 不寫在程式裡）"""
    token_path = os.path.expanduser("~/CCProject/.secrets/telegram_token.txt")
    if not os.path.exists(token_path):
        log("找不到 telegram token，略過通知")
        return
    try:
        with open(token_path, encoding="utf-8") as f:
            token = f.read().strip()
        urllib.request.urlopen(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=urllib.parse.urlencode({
                "chat_id": "7556217543",
                "text": text,
            }).encode(),
            timeout=10,
        )
    except Exception as e:
        log(f"Telegram 通知失敗：{type(e).__name__}: {e}")


def self_unload():
    """由 LaunchAgent 啟動時才自我卸載，否則 KeepAlive 會無限重跑"""
    if os.environ.get("XPC_SERVICE_NAME") != LAUNCHD_LABEL:
        return
    log("卸載 LaunchAgent...")
    result = subprocess.run(
        ["launchctl", "bootout", f"gui/{os.getuid()}/{LAUNCHD_LABEL}"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        log(f"⚠️ 卸載失敗（{result.returncode}）：{result.stderr.strip()}")


def main():
    os.makedirs(LOG_DIR, exist_ok=True)

    # 鎖住，避免 LaunchAgent 與手動執行同時跑，建出重複 VM 吃光免費額度
    lock_file = open(LOCK_PATH, "w")
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log("已有另一個搶機程序在執行，本次結束")
        return

    config = oci.config.from_file()
    # SDK 預設 timeout=None 等於永不逾時，連線靜默死掉時 KeepAlive 也救不回來
    compute_client = oci.core.ComputeClient(config, timeout=(10, 120))
    network_client = oci.core.VirtualNetworkClient(config, timeout=(10, 120))

    log("=== Oracle ARM VM 自動建立腳本 ===")
    log(f"目標：{TARGET_COUNT} 台 {SHAPE}（{OCPUS} OCPU / {MEMORY_IN_GBS} GB × {BOOT_VOLUME_GB} GB）")

    target_names = [f"{INSTANCE_PREFIX}-{i}" for i in range(1, TARGET_COUNT + 1)]

    # 先前已搶完：直接印結果並卸載，不再打 API
    if os.path.exists(RESULT_PATH):
        with open(RESULT_PATH, encoding="utf-8") as f:
            log("先前已完成，結果如下（要重搶請刪掉此檔）：\n" + f.read())
        self_unload()
        return

    ssh_public_key = generate_ssh_key()
    log("設定網路環境...")
    subnet_id = setup_network(network_client)

    # 每輪都重新問 OCI 現況，不依賴記憶體狀態，中斷重跑也不會漏掉任何一台。
    # 只有「3 台全部 RUNNING 且都拿到公開 IP」才算完成，逾時不算。
    attempt = 0
    permanent_errors = 0
    while True:
        attempt += 1
        ours = list_ours(compute_client)
        missing = [n for n in target_names if n not in ours]
        not_running = [
            n for n in target_names
            if n in ours and ours[n].lifecycle_state != "RUNNING"
        ]
        log(f"第 {attempt} 輪：已有 {len(ours)}/{TARGET_COUNT}，"
            f"未就緒 {', '.join(not_running) or '無'}，缺 {', '.join(missing) or '無'}")

        if permanent_errors >= 5:
            message = "Oracle 搶機連續 5 輪永久性錯誤，已停止（請看 log）"
            log(message)
            notify(message)
            self_unload()
            return

        # 1. 缺的就建
        for idx, name in enumerate(missing):
            try:
                instance = create_instance(compute_client, subnet_id, ssh_public_key, name)
                log(f"✅ {name} 建立成功（{instance.lifecycle_state}）")
                ours[name] = instance
                permanent_errors = 0
            except oci.exceptions.ServiceError as e:
                if e.status in (429, 500):
                    reason = "請求頻率過高" if e.status == 429 else "容量不足"
                    log(f"   {name}：{reason}")
                    permanent_errors = 0
                else:
                    # 不 raise：raise 會讓每 60 秒重啟都卡在同一台，其他台永遠輪不到
                    log(f"   {name}：HTTP {e.status} {e.code} {e.message}")
                    permanent_errors += 1
            except oci.exceptions.RequestException as e:
                # 網路層（逾時／連線中斷）不是 ServiceError 的子類，漏接會讓整支腳本 crash
                log(f"   {name}：網路錯誤 {type(e).__name__}，稍後重試")
                permanent_errors = 0
            if idx < len(missing) - 1:
                time.sleep(INTER_REQUEST_DELAY)
            if permanent_errors >= 5:
                break

        if missing or not_running:
            # STOPPED 的（例如被 Oracle 回收）嘗試重新啟動
            for name in not_running:
                if ours[name].lifecycle_state == "STOPPED":
                    log(f"{name} 目前 STOPPED，嘗試啟動...")
                    try:
                        compute_client.start_instance(ours[name].id)
                    except oci.exceptions.ServiceError as e:
                        log(f"   啟動失敗：{e.code} {e.message}")
            log(f"{RETRY_INTERVAL} 秒後再確認...")
            time.sleep(RETRY_INTERVAL)
            continue

        # 2. 三台都 RUNNING，取 IP；有缺就再等一輪，不寫完成檔
        report = []
        all_ready = True
        for name in target_names:
            ip = get_public_ip(compute_client, network_client, ours[name].id)
            report.append(f"{name}\t{ip or '（尚未取得 IP）'}")
            if ip:
                log(f"🌐 {name}：ssh -i {SSH_KEY_PATH} ubuntu@{ip}")
            else:
                all_ready = False
                log(f"⚠️ {name}：尚未取得公開 IP")

        if not all_ready:
            log(f"{RETRY_INTERVAL} 秒後再確認 IP...")
            time.sleep(RETRY_INTERVAL)
            continue
        break

    with open(RESULT_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(report) + "\n")
    log(f"結果已寫入 {RESULT_PATH}")
    notify("Oracle ARM VM 搶到了\n\n" + "\n".join(report))
    log("=== 全部完成 ===")
    self_unload()


if __name__ == "__main__":
    main()

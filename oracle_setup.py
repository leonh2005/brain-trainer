"""
Oracle Cloud 自動建立 ARM VM 腳本
- 沿用既有 VCN、子網路、Internet Gateway
- 產生 SSH 金鑰
- 持續嘗試建立 ARM VM（1 OCPU / 6 GB × 3 台），直到成功為止
"""

import os
import subprocess
import time
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
    return compute_client.launch_instance(oci.core.models.LaunchInstanceDetails(
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
    )).data


def list_existing_names(compute_client):
    """列出已存在（非 TERMINATED）的執行個體名稱，避免重跑時撞名 409"""
    instances = compute_client.list_instances(COMPARTMENT_ID).data
    return {i.display_name for i in instances}


def main():
    config = oci.config.from_file()
    compute_client = oci.core.ComputeClient(config)
    network_client = oci.core.VirtualNetworkClient(config)

    log("=== Oracle ARM VM 自動建立腳本 ===")
    log(f"目標：{TARGET_COUNT} 台 {SHAPE}（{OCPUS} OCPU / {MEMORY_IN_GBS} GB × {BOOT_VOLUME_GB} GB）")

    # 產生 SSH 金鑰
    ssh_public_key = generate_ssh_key()

    # 建立網路環境
    log("設定網路環境...")
    subnet_id = setup_network(network_client)

    # 已存在的名稱直接跳過（重跑時避免 409）
    existing = list_existing_names(compute_client)
    pending = [f"{INSTANCE_PREFIX}-{i}" for i in range(1, TARGET_COUNT + 1)]
    for name in list(pending):
        if name in existing:
            log(f"{name} 已存在，跳過")
            pending.remove(name)

    created = []
    attempt = 0
    while pending:
        attempt += 1
        log(f"第 {attempt} 輪嘗試（待建 {len(pending)} 台：{', '.join(pending)}）")

        names = list(pending)
        for idx, name in enumerate(names):
            try:
                instance = create_instance(compute_client, subnet_id, ssh_public_key, name)
                log(f"✅ {name} 建立成功（{instance.lifecycle_state}）")
                pending.remove(name)
                created.append((name, instance.id))
            except oci.exceptions.ServiceError as e:
                if e.status in (429, 500):
                    reason = "請求頻率過高" if e.status == 429 else "容量不足"
                    log(f"   {name}：{reason}")
                else:
                    log(f"❌ {name} 錯誤：{e.code} {e.message}")
                    raise
            if idx < len(names) - 1:
                time.sleep(INTER_REQUEST_DELAY)

        if pending:
            log(f"{RETRY_INTERVAL // 60} 分 {RETRY_INTERVAL % 60} 秒後重試...")
            time.sleep(RETRY_INTERVAL)

    # 全部受理後，等待 RUNNING 並印出連線資訊
    for name, instance_id in created:
        log(f"等待 {name} 啟動...")
        oci.wait_until(
            compute_client,
            compute_client.get_instance(instance_id),
            'lifecycle_state',
            'RUNNING',
            max_wait_seconds=600
        )
        vnic_attachments = compute_client.list_vnic_attachments(
            COMPARTMENT_ID, instance_id=instance_id
        ).data
        if vnic_attachments:
            vnic = network_client.get_vnic(vnic_attachments[0].vnic_id).data
            log(f"🌐 {name}：ssh -i {SSH_KEY_PATH} ubuntu@{vnic.public_ip}")

    log("=== 全部完成 ===")

    # 由 LaunchAgent 啟動時，全部成功後自我卸載，否則 KeepAlive 會無限重跑
    if os.environ.get("XPC_SERVICE_NAME") == LAUNCHD_LABEL:
        log("卸載 LaunchAgent...")
        subprocess.run(
            ["launchctl", "bootout", f"gui/{os.getuid()}/{LAUNCHD_LABEL}"],
            check=False
        )


if __name__ == "__main__":
    main()

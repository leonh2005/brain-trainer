"""
Oracle Cloud 自動建立 VM 腳本
- 自動建立 VCN、子網路、Internet Gateway
- 產生 SSH 金鑰
- 持續嘗試建立 ARM VM，直到成功為止
"""

import oci
import time
import os
import subprocess
from datetime import datetime

# ─── 設定 ────────────────────────────────────────────────────────
COMPARTMENT_ID = "ocid1.tenancy.oc1..aaaaaaaa33in6usn64ykyh2ji34rqmdxx3tpgk5xbugjptrn5jzhn3iw2vdq"
AVAILABILITY_DOMAIN = "BULW:AP-OSAKA-1-AD-1"
IMAGE_ID = "ocid1.image.oc1.ap-osaka-1.aaaaaaaa4icwwnrg4k7q6qbgwb7ojjnom23tsotvwg6yycwkwjsixf7hwndq"
SHAPE = "VM.Standard.E2.1.Micro"
INSTANCE_NAME = "line-bot"
SSH_KEY_PATH = os.path.expanduser("~/.ssh/oracle_line_bot")
RETRY_INTERVAL = 300  # 每 5 分鐘重試一次


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


def create_instance(compute_client, subnet_id, ssh_public_key):
    """嘗試建立 VM"""
    return compute_client.launch_instance(oci.core.models.LaunchInstanceDetails(
        compartment_id=COMPARTMENT_ID,
        availability_domain=AVAILABILITY_DOMAIN,
        display_name=INSTANCE_NAME,
        image_id=IMAGE_ID,
        shape=SHAPE,
        create_vnic_details=oci.core.models.CreateVnicDetails(
            subnet_id=subnet_id,
            assign_public_ip=True
        ),
        metadata={"ssh_authorized_keys": ssh_public_key}
    )).data


def main():
    config = oci.config.from_file()
    compute_client = oci.core.ComputeClient(config)
    network_client = oci.core.VirtualNetworkClient(config)

    log("=== Oracle VM 自動建立腳本 ===")

    # 產生 SSH 金鑰
    ssh_public_key = generate_ssh_key()

    # 建立網路環境
    log("設定網路環境...")
    subnet_id = setup_network(network_client)

    # 持續嘗試建立 VM
    attempt = 1
    while True:
        log(f"第 {attempt} 次嘗試建立 VM...")
        try:
            instance = create_instance(compute_client, subnet_id, ssh_public_key)
            log(f"✅ VM 建立成功！")
            log(f"Instance ID：{instance.id}")
            log(f"狀態：{instance.lifecycle_state}")
            log(f"等待 VM 啟動...")

            # 等待 VM 啟動完成
            oci.wait_until(
                compute_client,
                compute_client.get_instance(instance.id),
                'lifecycle_state',
                'RUNNING',
                max_wait_seconds=300
            )

            # 取得公開 IP
            vnic_attachments = compute_client.list_vnic_attachments(
                COMPARTMENT_ID, instance_id=instance.id
            ).data
            if vnic_attachments:
                vnic = network_client.get_vnic(vnic_attachments[0].vnic_id).data
                log(f"🌐 公開 IP：{vnic.public_ip}")
                log(f"\n連線指令：")
                log(f"ssh -i {SSH_KEY_PATH} ubuntu@{vnic.public_ip}")

            break

        except oci.exceptions.ServiceError as e:
            if e.status in (429, 500):
                reason = "請求頻率過高" if e.status == 429 else "容量不足"
                log(f"{reason}，{RETRY_INTERVAL // 60} 分鐘後重試...")
                time.sleep(RETRY_INTERVAL)
                attempt += 1
            else:
                log(f"❌ 錯誤：{e.message}")
                raise


if __name__ == "__main__":
    main()

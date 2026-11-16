# Smart 可选 WARP IPv4/IPv6 出站

**默认关闭；与 sing-box 安装完全独立。此扩展尚未进行真实 Cloudflare WARP 联网验收。**

## 功能边界

仅给你指定的一个 Smart 代理入站添加 userspace WireGuard endpoint 出站。
选择 ipv4、ipv6 或 dual，按目标 IP 地址族路由到 WireGuard peer。
其余代理入站和默认 direct 路由保留。不修改 VPS 主机路由、
SSH、DNS、防火墙、内核、IP 分配或既有其他代理服务。

这不会给 VPS 增加可供外部设备直接连接的公网 IPv4 或 IPv6。
只是给选定的代理流量增加一个可选的 IPv4/IPv6 出口。

目标为域名时，在路由匹配阶段可能尚未解析为 IP，此时 ip_version
条件不能保证命中。请在使用前自行验证目标地址解析与出站选择。
如果 WARP peer 离线，匹配的代理连接可能失败；不会自动切换备用 peer。

## 准备

1. Smart 专用 systemd 服务必须已经安装并处于运行状态。
2. 必须在本机另行合法准备 WireGuard-compatible profile（root 所有，
   权限 0600）。Smart 不申请 WARP 账号、不生成其注册凭据、不执行
   wgcf、warp-cli 或任何第三方一键安装脚本。
3. Profile 仅允许一个 Interface 和一个 Peer，含 PrivateKey、Address、
   PublicKey、Endpoint、AllowedIPs。Endpoint 必须是数字 IP:端口
   或 IPv6 的 [IPv6]:端口。拒绝脚本钩子、DNS 和系统路由选项。
4. ipv4 需要隧道 IPv4 地址及 0.0.0.0/0；ipv6 需要隧道 IPv6 地址及
   ::/0；dual 两者都需要。VPS 本身还必须能用 UDP 到达 peer 地址。
5. 请不要把配置、私钥或账户资料发送到聊天、Issue 或 GitHub 仓库。

## 使用命令（仅手动测试 VPS）

查询状态，只读：

    sudo bash install.sh --warp-status

先检查配置，不改变服务：

    sudo bash install.sh --warp-plan --profile /root/smart-warp-private.conf --inbound vless-reality-in --family ipv6

显式启用，仅影响指定入站与 IPv6 目标流量：

    sudo bash install.sh --warp-enable --profile /root/smart-warp-private.conf --inbound vless-reality-in --family ipv6 --confirm

显式关闭，核对哈希并恢复之前的 Smart 代理路由：

    sudo bash install.sh --warp-disable --confirm

把 ipv6 换成 ipv4 或 dual 可选择其他目标地址族。
也可以在 Smart 管理菜单选第 13 项。无需安装系统 WARP 客户端。

## 安全与恢复

- 仅允许 Smart 管理的 vless、hysteria2、tuic、anytls、socks、
  mixed、http 入站；拒绝 tun、tproxy、redirect 等系统流量入口。
- WireGuard endpoint 设置 system=false，不创建内核 WireGuard 接口。
- 先检查私有 Profile、配置结构和真实 sing-box check，再创建
  root-only 的 schema-v2 备份。显式确认后仅替换自己的 config.json
  并重启 Smart 独立服务。
- 启用成功后保存非秘密状态：入站名、目标地址族、回滚快照 ID 和
  配置指纹。WARP 私钥会进入受保护的 Smart 配置及其备份；这些都
  必须作为机密保护。
- 禁用前要求当前配置指纹与启用时一致，以防覆盖你后来手动更改的
  配置。禁用过程仅删除对应 Smart 路由规则和 endpoint，并再次备份。
- 若服务重启失败，尝试恢复原配置快照并重新启动独立服务。双重故障
  时必须使用独立的 VPS 服务商控制台恢复。
- 所有操作不安装内核包、不创建 VPN 系统设备、不执行路由命令、
  不调用 iptables / nftables / sysctl，不改 SSH 和 DNS。

## 测试状态

GitHub 隔离 CI 包含：严格配置解析、权限与链接拒绝、分 IP 家族路由、
明确启用/停用、故障模拟回滚、官方 sing-box v1.14.4 原生 WireGuard
endpoint 检查和无外部网络容器中的直接出站回归。

**尚未验证**：真实 Cloudflare WARP 账号注册、真实公网 IPv4/IPv6
WARP 出口、云网络/回程、真实系统重启和实际使用条款。不要将离线
配置验证理解为 Cloudflare 隧道已经连接。

参考：
- https://sing-box.sagernet.org/configuration/endpoint/wireguard/
- https://sing-box.sagernet.org/configuration/route/rule/
- https://developers.cloudflare.com/warp-client/get-started/linux/

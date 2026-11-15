# fscarmen/sing-box 安装器第一轮安全审计

**基准**：fscarmen/sing-box，提交 `7a666fc870d027790b6b831d219d8d99f13a73a4`，
存档于 `upstream/fscarmen/sing-box.sh`，Git blob SHA-1 `89ec86fbf37198c1ae3a865a63a1b83bd6178cae`。

**状态**：静态审计与自动门禁已完成，完整运行时/传递依赖审计仍未完成。
**禁止在用户 VPS 或 GitHub Runner 宿主机上直接执行原版脚本**。
命中某个风险模式不等于证明原作者存在后门；这是代码行为与攻击面评估。

## 优先修复的风险

| 风险 | 原版脚本位置（行号为固定提交） | 影响 | 安全改造要求 |
|---|---|---|---|
| 严重：远程脚本执行 | 1918、2330、6681–6686、6703–6706 | 上游/依赖仓库变化可在 root 权限下执行新代码 | 固定版本、校验哈希、审计依赖，禁止下载后直接执行 |
| 严重：跳过 TLS 验证 | 2293–2324、2560–2568、6570、6101 | 可能被中间人替换脚本、核心或模板 | 强制 HTTPS 校验，并验证官方签名/哈希 |
| 严重：潜在 Reality 私钥外传 | 4414 附近，`realitykey.cloudflare.now.cc/?privateKey=` | 触发该路径时，私钥出现在第三方请求 URL 中 | Reality 密钥计算完全本地化；绝不将私钥写入 URL |
| 严重：firewalld 区域策略 | 2613–2615，`--set-target=ACCEPT` | 改变 public zone 的默认允许策略，扩大攻击面 | 禁止全局修改；仅输出需手动放行的端口 |
| 高：NAT/防火墙更改 | 2623–2652、3306–3431 | 操作 iptables/ip6tables、UFW 文件、持久规则 | 默认不操作；独立预检、作用域限定、变更前快照 |
| 高：订阅路径绑定节点凭据 | 5388–5389、6016–6049 | 订阅 URL 可使用 HTTP，访问令牌与节点 UUID 关联 | 单独随机订阅令牌；本地生成；远端要求 HTTPS |
| 高：宽泛 Nginx 订阅路由 | 3957、4051–4069 | Nginx worker 设为 root，regex alias 捕获任意后缀，开启 autoindex | 仅 loopback，严格路径白名单，不开放目录列表 |
| 高：卸载范围过宽 | 6509–6519 | 可删除与其他部署共享的 /etc/sing-box、服务或 Nginx | 只清理项目自己的文件，禁止删除其他服务 |
| 中：第三方二维码 URL | 5953–5956 | 将含令牌的订阅地址交给外部二维码服务 | 本地离线生成二维码；不发送订阅 URL |

## 已采用的措施

1. 完全独立的 `scripts/local_subscriptions.py`：不引用第三方订阅转换接口，默认只绑定 127.0.0.1，订阅令牌可撤销。
2. 保留原版代码供比对，但只进行 `bash -n`、固定 Git blob 校验和静态风险扫描，**绝不通过 CI 直接运行**。
3. GitHub Actions 执行 `scripts/audit_upstream.py --strict`：若原版源码变化或关键风险特征不再符合已审查基准，必须重新审计。自有脚本出现危险下载/防火墙行为也会拒绝。
4. 所有真实 sing-box 核心测试在无网络、无特权的临时 Docker 环境进行。

## 本次审计不能证明的内容

- 静态正则扫描不是完整 Bash 解析器，可能漏掉经变量、函数或编码间接触发的行为。
- 不能保证已下载第三方二进制、Argo、WARP、Linux-NetSpeed、依赖仓库不存在额外风险。
- 原版安装器尚未在完整 Debian VM 中执行，其系统修改、重启恢复和卸载行为还未验收。
- 仅当完成代码改造和隔离整机测试后，才考虑发布可手动部署的安全版。

## 运行审计

```bash
python3 scripts/audit_upstream.py --strict
python3 scripts/audit_upstream.py --strict --report /tmp/fscarmen-audit.json
```

两条命令都只读取项目文件，不连接 VPS，不安装、不修改防火墙或系统服务。

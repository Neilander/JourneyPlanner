# -*- coding: utf-8 -*-
"""模拟：1 个酒店 → 10 个景点，高德通勤矩阵耗时。
三种打法对比。延迟参数按"国内服务器 + 复用连接"的保守估计。"""

N = 10                  # 景点数
PER_CALL = 0.06         # 单次 API 往返(复用连接, 国内): 60ms 保守值
FIRST_TLS = 0.18        # 首次 TLS 握手额外开销, 只发生一次
QPS_FREE = 3            # 免费个人版并发上限(保守, 路径规划类约 3 QPS)
QPS_PAID = 50           # 付费/企业版并发上限

def fmt(s): return f"{s*1000:.0f} ms" if s < 1 else f"{s:.2f} s"

print(f"场景：1 酒店 → {N} 景点\n")

# ---------- 打法 1：批量距离 API /v3/distance ----------
# 1 origin → N destinations 一次调用搞定(仅驾车/步行/直线, 不含公交)
t1 = FIRST_TLS + PER_CALL
print("【打法1】批量距离 API  /v3/distance")
print(f"  1 origin→{N} dest 一次调用拿全部 → 1 次请求")
print(f"  耗时 ≈ {fmt(t1)}  （含首次握手）/ 复用后 ≈ {fmt(PER_CALL)}")
print(f"  ✅ 驾车/步行矩阵首选。❌ 不支持公交。\n")

# ---------- 打法 2：逐个路径规划, 串行 ----------
t2 = FIRST_TLS + N * PER_CALL
print("【打法2】逐个路径规划 /direction/...  串行")
print(f"  {N} 个景点 = {N} 次请求, 一个接一个")
print(f"  耗时 ≈ {fmt(t2)}\n")

# ---------- 打法 3：逐个路径规划, 并发(受 QPS 限制) ----------
import math
def concurrent_time(n, qps, per_call, first_tls):
    # 分批: 每批 qps 个, 批数 = ceil(n/qps), 每批耗时一次 per_call
    batches = math.ceil(n / qps)
    return first_tls + batches * per_call

t3_free = concurrent_time(N, QPS_FREE, PER_CALL, FIRST_TLS)
t3_paid = concurrent_time(N, QPS_PAID, PER_CALL, FIRST_TLS)
print("【打法3】逐个路径规划, 并发")
print(f"  免费版(QPS≈{QPS_FREE}): {math.ceil(N/QPS_FREE)} 批 → ≈ {fmt(t3_free)}")
print(f"  付费版(QPS≈{QPS_PAID}): 1 批   → ≈ {fmt(t3_paid)}")
print("  公交矩阵只能走这条(公交必须逐对调用)。\n")

print("="*52)
print("结论：")
print(f"  驾车/步行 → 打法1, 1 次调用, ~{fmt(PER_CALL)}~{fmt(t1)}")
print(f"  公交     → 打法3并发, 免费版~{fmt(t3_free)} / 付费版~{fmt(t3_paid)}")
print(f"  最差(串行) ~{fmt(t2)}, 应避免")

==============================================
빠른 전략 재현 프롬프트 (One-liner)
==============================================

[복사해서 사용]

KOSPI-S&P500 베타 잔차 평균 회귀 전략을 Python으로 구현해주세요.

**데이터**: Excel (컬럼: 공통날짜, kospi_t, SPX_t-1, VIX_t-1, FX_t), ffill로 결측치 처리

**로직 순서**:
1. 로그 수익률: rK=log(kospi).diff(), rS=log(SPX).diff(), rFX=log(FX).diff()
2. Beta(60일): cov(rK,rS)/var(rS)
3. 잔차: resid = rK - beta×rS
4. Z-Score(60일): (resid - resid.rolling(60).mean().shift(1)) / resid.rolling(60).std().shift(1)  ← shift(1) 필수!
5. VIX필터: VIX.rolling(252).rank(pct=True).shift(1) ≤ 0.94  ← shift(1) 필수!
6. FX필터: |fx_z|.rolling(252).rank(pct=True).shift(1) ≤ 0.96  ← shift(1) 필수!
7. 신호(for loop, current_pos 상태 변수):
   - 필터 차단 → pos=0
   - |Z|≥7.095 → pos=0 (손절)
   - pos==0 and Z≤-2.15 → pos=+1 (Long)
   - pos==0 and Z≥+2.15 → pos=-1 (Short)
   - pos!=0 and |Z|≤0.0 → pos=0 (청산)
8. 수익률: pos.shift(1) × rK - 0.0002×|pos.diff()|

**파라미터**: ENTRY=2.15, EXIT=0.0, STOP=7.095, VIX_Q=0.94, FX_Q=0.96

**필수**: 모든 rolling 통계량에 shift(1) 적용 (Look-ahead bias 방지)

**출력**: Sharpe, MDD, 연환산 수익률, 거래 횟수

==============================================
상세 프롬프트 (Full Specification)
==============================================

[더 자세한 설명이 필요하면 STRATEGY_SPECIFICATION.md 참조]

다음 명세를 따라 KOSPI-S&P500 베타 잔차 평균 회귀 전략을 Python으로 구현해주세요.

## 1. 데이터 구조
- 입력: Excel 파일 (컬럼: 공통날짜, kospi_t, SPX_t-1, VIX_t-1, FX_t)
- 전처리:
  ```python
  df["공통날짜"] = pd.to_datetime(df["공통날짜"])
  df = df.sort_values("공통날짜").set_index("공통날짜")
  df = df.ffill().dropna()  # Forward fill for missing values
  ```

## 2. 전략 로직 (단계별)

### Step 1: 로그 수익률
```python
rK = np.log(kospi_t).diff()
rS = np.log(SPX_t-1).diff()
rFX = np.log(FX_t).diff()
```

### Step 2: Rolling Beta (60일)
```python
beta = rK.rolling(60).cov(rS) / rS.rolling(60).var()
```

### Step 3: 잔차 계산
```python
resid = rK - beta × rS
```

### Step 4: Z-Score 계산 (⚠️ Look-ahead Bias 방지)
```python
resid_mean = resid.rolling(60).mean().shift(1)  # ← shift(1) 필수!
resid_std = resid.rolling(60).std().shift(1)    # ← shift(1) 필수!
z = (resid - resid_mean) / resid_std
```

### Step 5: VIX 필터 (⚠️ Look-ahead Bias 방지)
```python
vix_rank = VIX_t-1.rolling(252).rank(pct=True).shift(1)  # ← shift(1) 필수!
allow_vix = (vix_rank <= 0.94)
```

### Step 6: FX 필터 (⚠️ Look-ahead Bias 방지)
```python
fx_mean = rFX.rolling(252).mean()
fx_std = rFX.rolling(252).std()
fx_z = (rFX - fx_mean) / fx_std
fx_shock = fx_z.abs().rolling(252).rank(pct=True).shift(1)  # ← shift(1) 필수!
allow_fx = (fx_shock <= 0.96)
```

### Step 7: 통합 필터
```python
allow = allow_vix & allow_fx
```

### Step 8: 신호 생성 (State Machine)
```python
pos = np.zeros(len(df))
z_arr = z.values
allow_arr = allow.values

ENTRY = 2.15
EXIT = 0.0
STOP_LOSS = 7.095

current_pos = 0

for i in range(1, len(df)):
    # Priority 1: Filter check
    if not allow_arr[i]:
        current_pos = 0
        pos[i] = 0
        continue

    if np.isnan(z_arr[i]):
        pos[i] = 0
        continue

    # Priority 2: Stop-loss
    if current_pos != 0:
        if abs(z_arr[i]) >= STOP_LOSS:
            current_pos = 0
            pos[i] = 0
            continue

    # Priority 3: Entry/Exit
    if current_pos == 0:
        if z_arr[i] <= -ENTRY:
            current_pos = +1  # Long
        elif z_arr[i] >= +ENTRY:
            current_pos = -1  # Short
    else:
        if abs(z_arr[i]) <= EXIT:
            current_pos = 0

    pos[i] = current_pos
```

### Step 9: 수익률 계산
```python
strategy_ret = pos.shift(1) × rK
turnover = pos.diff().abs()
transaction_cost = turnover × 0.0002
strategy_ret_net = strategy_ret - transaction_cost
equity = (1 + strategy_ret_net.fillna(0)).cumprod()
```

## 3. 파라미터 (Grid Search 최적화 완료)
```python
class StrategyParams:
    BETA_WINDOW = 60
    RESID_WINDOW = 60
    FILTER_WINDOW = 252

    ENTRY_THRESHOLD = 2.15
    EXIT_THRESHOLD = 0.0
    STOP_LOSS_MULTIPLIER = 3.3  # Z = 2.15 × 3.3 = 7.095

    VIX_QUANTILE = 0.94
    FX_QUANTILE = 0.96

    TRANSACTION_COST = 0.0002
```

## 4. 성과 지표 계산
```python
def compute_summary(df):
    ann_factor = 252
    valid_ret = df["strategy_ret_net"].dropna()

    ann_return = valid_ret.mean() * ann_factor
    ann_vol = valid_ret.std() * np.sqrt(ann_factor)
    sharpe = ann_return / ann_vol if ann_vol > 0 else 0.0

    equity = (1 + valid_ret).cumprod()
    mdd = (equity / equity.cummax() - 1).min()

    hit_ratio = (valid_ret > 0).mean()

    return {
        "ann_return": ann_return,
        "ann_vol": ann_vol,
        "sharpe": sharpe,
        "mdd": mdd,
        "hit_ratio": hit_ratio
    }
```

## 5. 예상 결과 (26년 백테스트)
- 연환산 수익률: 8.39%
- Sharpe Ratio: 0.75
- MDD: -15.72%
- 총 거래 횟수: 198회
- 포지션 보유율: 35.6%

## 6. 필수 구현 원칙
1. **Look-ahead Bias 방지**: 모든 rolling 통계량에 shift(1) 적용
2. **포지션 로직**: current_pos 변수로 상태 머신 구현
3. **필터 우선순위**: 필터 → 손절매 → 진입/청산 순서 엄수

## 7. 검증 방법
```python
# 1. Look-ahead bias 체크
assert df["resid_mean"].isna().sum() > df["resid"].rolling(60).mean().isna().sum()

# 2. 필터 동작 확인
assert (~df["allow"]).sum() > 0

# 3. 필터 차단 시 포지션 = 0
assert df.loc[~df["allow"], "pos"].abs().sum() == 0

# 4. 성과 지표 범위 확인 (±5%)
assert 7.0 < ann_return < 9.5
assert 0.65 < sharpe < 0.85
assert -18.0 < mdd < -13.0
```

==============================================
개별 함수 재현 프롬프트
==============================================

## [1] Z-Score 계산 함수

```
pandas DataFrame에서 잔차의 Z-Score를 계산하는 함수를 작성해주세요.

입력: df with column 'resid'
출력: df with columns ['resid_mean', 'resid_std', 'z']

구현:
resid_mean = resid.rolling(60).mean().shift(1)
resid_std = resid.rolling(60).std().shift(1)
z = (resid - resid_mean) / resid_std

중요: shift(1)을 빠뜨리면 Look-ahead bias 발생!
```

## [2] 필터 계산 함수

```
VIX와 FX 리스크 필터를 계산하는 함수를 작성해주세요.

입력: df with columns ['VIX_t-1', 'rFX']
출력: df with column 'allow' (bool)

구현:
1. vix_rank = VIX_t-1.rolling(252).rank(pct=True).shift(1)
   allow_vix = (vix_rank <= 0.94)

2. fx_z = (rFX - rFX.rolling(252).mean()) / rFX.rolling(252).std()
   fx_shock = fx_z.abs().rolling(252).rank(pct=True).shift(1)
   allow_fx = (fx_shock <= 0.96)

3. allow = allow_vix & allow_fx

중요: rank()는 현재 값을 포함하므로 shift(1) 필수!
```

## [3] 신호 생성 함수

```
Z-Score와 필터를 기반으로 매매 신호를 생성하는 함수를 작성해주세요.

입력:
- z: Z-Score array
- allow: Filter array (bool)
- ENTRY=2.15, EXIT=0.0, STOP_LOSS=7.095

출력: position array (0, +1, -1)

로직 (for loop):
for i in range(1, len(z)):
    if not allow[i]:
        pos[i] = 0
        continue

    if current_pos != 0 and abs(z[i]) >= STOP_LOSS:
        pos[i] = 0
        continue

    if current_pos == 0:
        if z[i] <= -ENTRY: pos[i] = +1
        elif z[i] >= +ENTRY: pos[i] = -1
    else:
        if abs(z[i]) <= EXIT: pos[i] = 0

구현 방식: current_pos 상태 변수 사용
```

==============================================

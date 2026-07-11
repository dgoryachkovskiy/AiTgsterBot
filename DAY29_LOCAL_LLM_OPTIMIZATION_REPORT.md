# Day 29. Local LLM Optimization

## Summary

- generated_at: `2026-07-10T14:10:08+00:00`
- task: `AstroTarot local RAG`
- model: `qwen2.5:0.5b`
- architecture: `qwen2`
- parameters: `494.03M`
- context_length: `32768`
- quantization: `Q4_K_M`
- baseline_options: `{'temperature': 0.1}`
- optimized_options: `{'temperature': 0.0, 'num_ctx': 4096, 'num_predict': 90, 'top_p': 0.7, 'repeat_penalty': 1.05}`
- questions: `5`
- baseline_quality_ok: `2`
- optimized_quality_ok: `2`
- avg_retrieval_seconds: `0.094`
- avg_baseline_seconds: `0.577`
- avg_optimized_seconds: `0.489`
- avg_baseline_working_set_mb: `1418.82`
- avg_optimized_working_set_mb: `1422.73`
- avg_baseline_private_memory_mb: `3801.49`
- avg_optimized_private_memory_mb: `3805.41`
- optimized_wins: `4`

## Comparison

| question | retrieval s | baseline quality | baseline s | optimized quality | optimized s | winner |
|---|---:|---|---:|---|---:|---|
| Какой endpoint Android клиент вызывает для проверки email-ко ...[truncated] | 0.095 | ok | 0.840 | ok | 0.485 | optimized_speed |
| Какой endpoint используется для повторной отправки email verification code? | 0.102 | ok | 0.315 | ok | 0.630 | tie_quality |
| Какая минимальная длина пароля проверяется при регистрации? | 0.091 | weak | 0.437 | weak | 0.354 | optimized_quality |
| Какие основные Screen routes объявлены в навигации AstroTarot? | 0.078 | weak | 0.931 | weak | 0.634 | optimized_speed |
| Когда показывается BottomNavigationBar? | 0.105 | weak | 0.362 | weak | 0.344 | optimized_speed |

## Prompt Optimization

- baseline: simple Day28 RAG prompt, `temperature=0.1`, no explicit context/token limit.
- optimized: AstroTarot-specific RAG prompt for exact endpoint/field extraction and insufficient-context rule.
- optimized options tune `temperature`, `num_ctx`, `num_predict`, `top_p`, `repeat_penalty`.
- quantization is read from real Ollama metadata; installed model reports the active quantized format.

## Details

### Какой endpoint Android клиент вызывает для проверки email-кода и какие поля отправляет?

- retrieval_seconds: `0.095`
- baseline_quality: `ok`
- baseline_seconds: `0.84`
- baseline_citations: `[]`
- baseline_expected_terms: `['/v1/auth/verify-email-code', 'email', 'code', 'verifyEmailCode']`
- baseline_working_set_mb: `1416.23`
- baseline_private_memory_mb: `3798.93`
- optimized_quality: `ok`
- optimized_seconds: `0.485`
- optimized_citations: `[]`
- optimized_expected_terms: `['/v1/auth/verify-email-code', 'email', 'code']`
- optimized_working_set_mb: `1422.55`
- optimized_private_memory_mb: `3805.34`
- winner: `optimized_speed`

**Sources:**

- [S1] `app\src\main\java\com\gorya\astrotarot\data\repository\AuthRepository.kt` | `suspend fun verifyEmailCode(email: String, code: String): Result<Unit> =` | score `0.822817`
- [S2] `app\src\main\java\com\gorya\astrotarot\data\backend\BackendApiClient.kt` | `suspend fun verifyEmailCode(email: String, code: String) {` | score `0.753612`
- [S3] `backend\src\main\kotlin\com\gorya\astrotarot\backend\Models.kt` | `data class VerifyEmailCodeRequest(val email: String, val code: String)` | score `0.730841`
- [S4] `app\src\main\java\com\gorya\astrotarot\ui\signup\SignUpViewModel.kt` | `private fun verifyCode() {` | score `0.712977`
- [S5] `app\src\main\java\com\gorya\astrotarot\ui\LoginViewModel.kt` | `private fun mapBackendError(error: BackendApiException, google: Boolean): String =` | score `0.682518`

**Baseline Answer:**

Android клиент вызывает endpoint `/v1/auth/verify-email-code` для проверки email-кода. Значения поля отправляются в `VerifyEmailCodeRequest` класса, который содержит:

- `email`: Email-адрес для проверки
- `code`: 6-значный код для проверки

Это endpoint используется для отправки запроса на сервер для проверки email-кода.

**Optimized Answer:**

Android клиент вызывает endpoint `/v1/auth/verify-email-code` для проверки email-кода и отправляет `email`, `code` и `verificationCode` полями.

### Какой endpoint используется для повторной отправки email verification code?

- retrieval_seconds: `0.102`
- baseline_quality: `ok`
- baseline_seconds: `0.315`
- baseline_citations: `[]`
- baseline_expected_terms: `['email', 'resendEmailVerification']`
- baseline_working_set_mb: `1419.55`
- baseline_private_memory_mb: `3802.26`
- optimized_quality: `ok`
- optimized_seconds: `0.63`
- optimized_citations: `[]`
- optimized_expected_terms: `['/v1/auth/resend-verification', 'email', 'resendEmailVerification']`
- optimized_working_set_mb: `1428.05`
- optimized_private_memory_mb: `3810.82`
- winner: `tie_quality`

**Sources:**

- [S1] `app\src\main\java\com\gorya\astrotarot\data\backend\BackendApiClient.kt` | `suspend fun resendEmailVerification(email: String) {` | score `0.765672`
- [S2] `app\src\main\java\com\gorya\astrotarot\data\repository\AuthRepository.kt` | `suspend fun resendEmailVerification(email: String): Result<Unit> =` | score `0.76512`
- [S3] `backend\src\main\kotlin\com\gorya\astrotarot\backend\Models.kt` | `data class ResendEmailVerificationRequest(val email: String)` | score `0.713421`
- [S4] `app\src\main\java\com\gorya\astrotarot\data\repository\AuthRepository.kt` | `suspend fun signUp(email: String, password: String, displayName: String?): Result<Unit> =` | score `0.704021`
- [S5] `app\src\main\java\com\gorya\astrotarot\ui\signup\SignUpViewModel.kt` | `private fun resendCode() {` | score `0.691785`

**Baseline Answer:**

resendEmailVerification(email: String)

**Optimized Answer:**

Вот как можно найти endpoint для повторной отправки кода для электронной почты:

1. **S1**: `/v1/auth/resend-verification`
2. **S2**: `/auth/resend-verification`
3. **S3**: `runAuth { backendApiClient.resendEmailVerification(email) }`
4. **S4**: `runAuth { val response = backendApiClient.signUp(email, password, displayName

### Какая минимальная длина пароля проверяется при регистрации?

- retrieval_seconds: `0.091`
- baseline_quality: `weak`
- baseline_seconds: `0.437`
- baseline_citations: `[]`
- baseline_expected_terms: `[]`
- baseline_working_set_mb: `1416.07`
- baseline_private_memory_mb: `3798.76`
- optimized_quality: `weak`
- optimized_seconds: `0.354`
- optimized_citations: `[]`
- optimized_expected_terms: `['8']`
- optimized_working_set_mb: `1418.38`
- optimized_private_memory_mb: `3801.01`
- winner: `optimized_quality`

**Sources:**

- [S1] `app\src\main\java\com\gorya\astrotarot\ui\signup\PasswordRecoveryViewModel.kt` | `class PasswordRecoveryViewModel @Inject constructor(` | score `0.654682`
- [S2] `app\src\main\java\com\gorya\astrotarot\ui\signup\SignUpViewModel.kt` | `class SignUpViewModel @Inject constructor(` | score `0.6484`
- [S3] `app\src\main\java\com\gorya\astrotarot\ui\signup\SignUpIntent.kt` | `data class PasswordChanged(val password: String) : SignUpIntent()` | score `0.645118`
- [S4] `app\src\main\java\com\gorya\astrotarot\ui\signup\PasswordRecoveryIntent.kt` | `file` | score `0.643746`
- [S5] `backend\README.md` | `Required environment` | score `0.638786`

**Baseline Answer:**

минимальная длина пароля проверяется при регистрации в AstroTarot кодеbase, так как это требует минимального количества символов для безопасной пароля.

**Optimized Answer:**

минимальная длина пароля проверяется при регистрации 8 символов.

### Какие основные Screen routes объявлены в навигации AstroTarot?

- retrieval_seconds: `0.078`
- baseline_quality: `weak`
- baseline_seconds: `0.931`
- baseline_citations: `[]`
- baseline_expected_terms: `['signup', 'tarot']`
- baseline_working_set_mb: `1419.03`
- baseline_private_memory_mb: `3801.63`
- optimized_quality: `weak`
- optimized_seconds: `0.634`
- optimized_citations: `[]`
- optimized_expected_terms: `['signup', 'tarot']`
- optimized_working_set_mb: `1423.54`
- optimized_private_memory_mb: `3806.17`
- winner: `optimized_speed`

**Sources:**

- [S1] `app\src\main\java\com\gorya\astrotarot\ui\signup\PasswordRecoveryIntent.kt` | `file` | score `0.749182`
- [S2] `app\src\main\java\com\gorya\astrotarot\ui\signup\SignUpIntent.kt` | `file` | score `0.704163`
- [S3] `app\src\main\java\com\gorya\astrotarot\ui\Navigation.kt` | `file` | score `0.690369`
- [S4] `app\src\main\java\com\gorya\astrotarot\ui\intent\LoginIntent.kt` | `file` | score `0.650869`
- [S5] `app\src\main\java\com\gorya\astrotarot\ui\screens\TarotViewModel.kt` | `data class OpenSpread(val spread: TarotSpread) : TarotIntent` | score `0.645769`

**Baseline Answer:**

Основные Screen routes объявлены в навигации AstroTarot:

- **PasswordRecoveryIntent**: Объявлены основные Screen routes для входа и восстановления пароля.
  - `com.gorya.astrotarot.ui.signup.intent.PasswordRecoveryIntent`
  
- **SignUpIntent**: Объявлены основные Screen routes для регистрации.
  - `com.gorya.astrotarot.ui.signup.intent.SignUpIntent`

- **Navigation**: Объявлены основные Screen routes для перехода между экранами и меню.
  - `com.gorya.astrotarot.ui.navigation.NavHostController`
  - `com.gorya.astrotarot.ui.components.BottomNavigationBar`

**Optimized Answer:**

Основные ScreenRoutes объявлены в навигации AstroTarot:

1. **PasswordRecoveryIntent**:
   - **URL**: /signup/PasswordRecoveryIntent
   - **Route**: /signup/PasswordRecoveryIntent
   - **Description**: Объявление основного экрана для входа в систему.

2. **SignUpIntent**:
   - **URL**: /signup/SignUpIntent

### Когда показывается BottomNavigationBar?

- retrieval_seconds: `0.105`
- baseline_quality: `weak`
- baseline_seconds: `0.362`
- baseline_citations: `[]`
- baseline_expected_terms: `['BottomNavigationBar']`
- baseline_working_set_mb: `1423.23`
- baseline_private_memory_mb: `3805.85`
- optimized_quality: `weak`
- optimized_seconds: `0.344`
- optimized_citations: `[]`
- optimized_expected_terms: `['BottomNavigationBar', 'home']`
- optimized_working_set_mb: `1421.12`
- optimized_private_memory_mb: `3803.7`
- winner: `optimized_speed`

**Sources:**

- [S1] `app\src\main\java\com\gorya\astrotarot\ui\Navigation.kt` | `private fun NavHostController.navigateBottom(route: String) {` | score `0.644873`
- [S2] `app\src\main\res\values\strings.xml` | `<string name="nav_horoscope">Horoscope</string>` | score `0.627108`
- [S3] `app\src\main\java\com\gorya\astrotarot\ui\Navigation.kt` | `private fun NavHostController.navigateHome() {` | score `0.621388`
- [S4] `app\src\main\java\com\gorya\astrotarot\ui\state\HomeState.kt` | `data object NavigateTarot : HomeEffect` | score `0.611667`
- [S5] `app\src\main\java\com\gorya\astrotarot\ui\screens\TarotViewModel.kt` | `data object BackFromSpreadResult : TarotIntent` | score `0.609623`

**Baseline Answer:**

BottomNavigationBar показывается при нажатии на кнопку "Back" в верхней части экрана.

**Optimized Answer:**

Самый верхний уровень BottomNavigationBar показывается при вызове метода navigateHome().

## Commands

```powershell
.\.venv\Scripts\python.exe day29_local_llm_optimizer.py status
.\.venv\Scripts\python.exe day29_local_llm_optimizer.py compare q01
.\.venv\Scripts\python.exe day29_local_llm_optimizer.py verify --limit 5
.\.venv\Scripts\python.exe day29_local_llm_optimizer.py resources
```

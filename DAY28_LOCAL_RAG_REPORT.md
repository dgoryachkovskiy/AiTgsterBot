# Day 28. Local LLM + RAG

## Summary

- generated_at: `2026-07-09T11:19:13+00:00`
- fully_local_rag: `True`
- retrieval: `local SQLite index + local Ollama embeddings`
- generation: `local Ollama chat model`
- index_dir: `C:\Users\pospi\Documents\Codex\day21_index_store`
- local_model: `qwen2.5:0.5b`
- cloud_compare: `False`
- questions: `3`
- local_passed: `3`
- cloud_passed: `0`
- local_quality_ok: `0`
- cloud_quality_ok: `0`
- avg_retrieval_seconds: `0.105`
- avg_local_seconds: `0.163`
- avg_cloud_seconds: `skipped`

## Comparison

| question | chunks | retrieval s | local quality | local s | cloud quality | cloud s | faster |
|---|---:|---:|---|---:|---|---:|---|
| Какой endpoint Android клиент вызывает для проверки email-ко ...[truncated] | 5 | 0.106 | weak | 0.255 | skip | skip | local-only |
| Какой endpoint используется для повторной отправки email verification code? | 5 | 0.111 | weak | 0.123 | skip | skip | local-only |
| Какая минимальная длина пароля проверяется при регистрации? | 5 | 0.097 | weak | 0.110 | skip | skip | local-only |

## Details

### Какой endpoint Android клиент вызывает для проверки email-кода и какие поля отправляет?

- retrieval_elapsed_seconds: `0.106`
- local_model: `qwen2.5:0.5b`
- local_elapsed_seconds: `0.255`
- cloud_model: `skipped`
- cloud_elapsed_seconds: `skipped`
- local_quality: `weak`
- local_stable: `True`
- local_has_source_citations: `False`
- cloud_quality: `skipped`
- cloud_stable: `skipped`
- faster_provider: `local-only`

**Retrieved Sources:**

- [S1] `app\src\main\java\com\gorya\astrotarot\data\repository\AuthRepository.kt` | `suspend fun verifyEmailCode(email: String, code: String): Result<Unit> =` | score `0.822817`
- [S2] `app\src\main\java\com\gorya\astrotarot\data\backend\BackendApiClient.kt` | `suspend fun verifyEmailCode(email: String, code: String) {` | score `0.753612`
- [S3] `backend\src\main\kotlin\com\gorya\astrotarot\backend\Models.kt` | `data class VerifyEmailCodeRequest(val email: String, val code: String)` | score `0.730841`
- [S4] `app\src\main\java\com\gorya\astrotarot\ui\signup\SignUpViewModel.kt` | `private fun verifyCode() {` | score `0.712977`
- [S5] `app\src\main\java\com\gorya\astrotarot\ui\LoginViewModel.kt` | `private fun mapBackendError(error: BackendApiException, google: Boolean): String =` | score `0.682518`

**Local Answer:**

Android клиент вызывает endpoint `/v1/auth/verify-email-code` для проверки email-кода. Значения поля отправляются в запросе `JSONObject`.

**Cloud Answer:**

skipped

### Какой endpoint используется для повторной отправки email verification code?

- retrieval_elapsed_seconds: `0.111`
- local_model: `qwen2.5:0.5b`
- local_elapsed_seconds: `0.123`
- cloud_model: `skipped`
- cloud_elapsed_seconds: `skipped`
- local_quality: `weak`
- local_stable: `True`
- local_has_source_citations: `False`
- cloud_quality: `skipped`
- cloud_stable: `skipped`
- faster_provider: `local-only`

**Retrieved Sources:**

- [S1] `app\src\main\java\com\gorya\astrotarot\data\backend\BackendApiClient.kt` | `suspend fun resendEmailVerification(email: String) {` | score `0.765672`
- [S2] `app\src\main\java\com\gorya\astrotarot\data\repository\AuthRepository.kt` | `suspend fun resendEmailVerification(email: String): Result<Unit> =` | score `0.76512`
- [S3] `backend\src\main\kotlin\com\gorya\astrotarot\backend\Models.kt` | `data class ResendEmailVerificationRequest(val email: String)` | score `0.713421`
- [S4] `app\src\main\java\com\gorya\astrotarot\data\repository\AuthRepository.kt` | `suspend fun signUp(email: String, password: String, displayName: String?): Result<Unit> =` | score `0.704021`
- [S5] `app\src\main\java\com\gorya\astrotarot\ui\signup\SignUpViewModel.kt` | `private fun resendCode() {` | score `0.691785`

**Local Answer:**

resendEmailVerification

**Cloud Answer:**

skipped

### Какая минимальная длина пароля проверяется при регистрации?

- retrieval_elapsed_seconds: `0.097`
- local_model: `qwen2.5:0.5b`
- local_elapsed_seconds: `0.11`
- cloud_model: `skipped`
- cloud_elapsed_seconds: `skipped`
- local_quality: `weak`
- local_stable: `True`
- local_has_source_citations: `False`
- cloud_quality: `skipped`
- cloud_stable: `skipped`
- faster_provider: `local-only`

**Retrieved Sources:**

- [S1] `app\src\main\java\com\gorya\astrotarot\ui\signup\PasswordRecoveryViewModel.kt` | `class PasswordRecoveryViewModel @Inject constructor(` | score `0.654682`
- [S2] `app\src\main\java\com\gorya\astrotarot\ui\signup\SignUpViewModel.kt` | `class SignUpViewModel @Inject constructor(` | score `0.6484`
- [S3] `app\src\main\java\com\gorya\astrotarot\ui\signup\SignUpIntent.kt` | `data class PasswordChanged(val password: String) : SignUpIntent()` | score `0.645118`
- [S4] `app\src\main\java\com\gorya\astrotarot\ui\signup\PasswordRecoveryIntent.kt` | `file` | score `0.643746`
- [S5] `backend\README.md` | `Required environment` | score `0.638786`

**Local Answer:**

3

**Cloud Answer:**

skipped

## Commands

```powershell
.\.venv\Scripts\python.exe day28_local_rag.py status
.\.venv\Scripts\python.exe day28_local_rag.py ask q01 --local-model qwen2.5:0.5b
.\.venv\Scripts\python.exe day28_local_rag.py compare q01 --local-model qwen2.5:0.5b
.\.venv\Scripts\python.exe day28_local_rag.py verify --limit 3 --local-model qwen2.5:0.5b
```

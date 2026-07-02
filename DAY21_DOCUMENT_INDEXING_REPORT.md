# Day 21. Document Indexing

## Summary

- source: `C:\Users\pospi\AndroidStudioProjects\AstroTarot`
- documents: `215`
- estimated_pages: `579.62`
- index: `C:\Users\pospi\Documents\Codex\day21_index_store\astro_tarot_rag_index.sqlite`
- embedding_provider: `ollama`
- embedding_model: `nomic-embed-text`
- embedding_base_url: `http://localhost:11434`
- embedding_dim: `768`

## Chunking Strategies

| strategy | chunks | avg tokens | p95 tokens | sources | description |
|---|---:|---:|---:|---:|---|
| fixed | 557 | 187.76 | 240.0 | 215 | Fixed token windows: 240 tokens, overlap 40. |
| structure | 1489 | 61.05 | 360.0 | 215 | Split by headings/classes/functions/XML nodes, max 360 tokens per section. |

## Retrieval Comparison

```json
{
  "email login verification code": {
    "fixed": {
      "top_sources": [
        "backend\\src\\main\\kotlin\\com\\gorya\\astrotarot\\backend\\Application.kt",
        "backend\\src\\main\\kotlin\\com\\gorya\\astrotarot\\backend\\Application.kt",
        "app\\src\\main\\java\\com\\gorya\\astrotarot\\ui\\signup\\SignUpScreen.kt",
        "backend\\src\\main\\kotlin\\com\\gorya\\astrotarot\\backend\\Repositories.kt",
        "app\\src\\main\\java\\com\\gorya\\astrotarot\\ui\\signup\\PasswordRecoveryIntent.kt"
      ],
      "top_sections": [
        "fun Application.astrotarotModule(config: AppConfig, database: Database) {",
        "file",
        "fun SignUpScreen(",
        "fun refresh(refreshToken: String): AuthResponse {",
        "file"
      ],
      "avg_top5_score": 0.6473,
      "distinct_files": 4
    },
    "structure": {
      "top_sources": [
        "app\\src\\main\\java\\com\\gorya\\astrotarot\\ui\\signup\\SignUpViewModel.kt",
        "backend\\src\\main\\kotlin\\com\\gorya\\astrotarot\\backend\\Models.kt",
        "backend\\src\\main\\kotlin\\com\\gorya\\astrotarot\\backend\\Models.kt",
        "app\\src\\main\\java\\com\\gorya\\astrotarot\\data\\backend\\BackendApiClient.kt",
        "app\\src\\main\\java\\com\\gorya\\astrotarot\\data\\repository\\AuthRepository.kt"
      ],
      "top_sections": [
        "private fun verifyCode() {",
        "data class SignUpResponse(",
        "data class VerifyEmailCodeRequest(val email: String, val code: String)",
        "suspend fun verifyEmailCode(email: String, code: String) {",
        "suspend fun verifyEmailCode(email: String, code: String): Result<Unit> ="
      ],
      "avg_top5_score": 0.7094,
      "distinct_files": 4
    }
  },
  "tarot card reading screen": {
    "fixed": {
      "top_sources": [
        "backend\\src\\main\\kotlin\\com\\gorya\\astrotarot\\backend\\Models.kt",
        "app\\src\\main\\java\\com\\gorya\\astrotarot\\data\\local\\dao\\TarotCardDao.kt",
        "app\\src\\main\\java\\com\\gorya\\astrotarot\\domain\\model\\TarotCard.kt",
        "app\\src\\test\\java\\com\\gorya\\astrotarot\\ui\\screens\\TarotResultStageTest.kt",
        "backend\\src\\main\\kotlin\\com\\gorya\\astrotarot\\backend\\Models.kt"
      ],
      "top_sections": [
        "data class AiTextResponse(val text: String, val source: String, val date: String)",
        "file",
        "file",
        "file",
        "file"
      ],
      "avg_top5_score": 0.5713,
      "distinct_files": 4
    },
    "structure": {
      "top_sources": [
        "app\\src\\main\\java\\com\\gorya\\astrotarot\\ui\\Navigation.kt",
        "app\\src\\main\\java\\com\\gorya\\astrotarot\\ui\\screens\\TarotViewModel.kt",
        "app\\src\\main\\java\\com\\gorya\\astrotarot\\data\\local\\dao\\TarotCardDao.kt",
        "app\\src\\main\\java\\com\\gorya\\astrotarot\\ui\\screens\\TarotScreen.kt",
        "app\\src\\test\\java\\com\\gorya\\astrotarot\\data\\repository\\TarotDeckRepositoryTest.kt"
      ],
      "top_sections": [
        "data object Tarot : Screen(\"tarot\")",
        "data class SelectDeck(val deck: TarotDeckChoice) : TarotIntent",
        "suspend fun upsertAll(cards: List<TarotCardEntity>)",
        "private fun MiniSpreadImage(card: TarotCard) {",
        "class TarotDeckRepositoryTest {"
      ],
      "avg_top5_score": 0.6679,
      "distinct_files": 5
    }
  },
  "backend authentication endpoint": {
    "fixed": {
      "top_sources": [
        "backend\\README.md",
        "app\\src\\main\\java\\com\\gorya\\astrotarot\\data\\backend\\SessionRepository.kt",
        "app\\src\\main\\java\\com\\gorya\\astrotarot\\ui\\signup\\PasswordRecoveryIntent.kt",
        "app\\src\\main\\java\\com\\gorya\\astrotarot\\domain\\usecase\\SignUpUseCase.kt",
        "app\\src\\main\\res\\xml\\network_security_config.xml"
      ],
      "top_sections": [
        "markdown",
        "fun clearMemory() {",
        "file",
        "file",
        "file"
      ],
      "avg_top5_score": 0.5344,
      "distinct_files": 5
    },
    "structure": {
      "top_sources": [
        "app\\src\\main\\java\\com\\gorya\\astrotarot\\data\\backend\\BackendApiClient.kt",
        "app\\src\\main\\java\\com\\gorya\\astrotarot\\data\\repository\\AuthRepository.kt",
        "app\\src\\main\\java\\com\\gorya\\astrotarot\\data\\backend\\SessionRepository.kt",
        "backend\\README.md",
        "app\\src\\main\\java\\com\\gorya\\astrotarot\\data\\backend\\BackendApiClient.kt"
      ],
      "top_sections": [
        "data class BackendAuthResponse(",
        "class AuthRepository @Inject constructor(",
        "data class BackendSession(",
        "Main endpoints",
        "data class BackendSignUpResponse("
      ],
      "avg_top5_score": 0.6262,
      "distinct_files": 4
    }
  },
  "Jetpack Compose navigation": {
    "fixed": {
      "top_sources": [
        "app\\build.gradle.kts",
        "gradle\\libs.versions.toml",
        "backend\\src\\main\\kotlin\\com\\gorya\\astrotarot\\backend\\AiService.kt",
        "app\\src\\main\\java\\com\\gorya\\astrotarot\\ui\\StartViewModel.kt",
        "app\\src\\main\\java\\com\\gorya\\astrotarot\\ui\\components\\BottomNavigationBar.kt"
      ],
      "top_sections": [
        "file",
        "file",
        "private fun parseJsonObject(raw: String): JsonObject {",
        "file",
        "file"
      ],
      "avg_top5_score": 0.515,
      "distinct_files": 5
    },
    "structure": {
      "top_sources": [
        "app\\build.gradle.kts",
        "app\\src\\main\\java\\com\\gorya\\astrotarot\\ui\\Navigation.kt",
        "app\\src\\main\\java\\com\\gorya\\astrotarot\\ui\\screens\\TarotViewModel.kt",
        "app\\src\\main\\java\\com\\gorya\\astrotarot\\ui\\LoginState.kt",
        "app\\src\\main\\java\\com\\gorya\\astrotarot\\ui\\Navigation.kt"
      ],
      "top_sections": [
        "file",
        "private fun NavHostController.navigateWelcomeReset() {",
        "data object NavigateDreams : TarotEffect",
        "data object NavigateSignUp : LoginEffect",
        "private fun NavHostController.navigateHome() {"
      ],
      "avg_top5_score": 0.5675,
      "distinct_files": 4
    }
  }
}
```

## What Is Stored Per Chunk

- `source`: relative file path
- `title`: file name
- `section`: heading/class/function/XML node/file
- `chunk_id`: stable strategy/doc/index id
- `text`: chunk text
- `embedding`: normalized float32 vector blob

## Check Commands

```powershell
ollama serve
ollama pull nomic-embed-text
.\.venv\Scripts\python.exe day21_document_indexer.py check-ollama
.\.venv\Scripts\python.exe day21_document_indexer.py build --embedder ollama
.\.venv\Scripts\python.exe day21_document_indexer.py stats
.\.venv\Scripts\python.exe day21_document_indexer.py compare
.\.venv\Scripts\python.exe day21_document_indexer.py search "email login verification code" --strategy fixed
.\.venv\Scripts\python.exe day21_document_indexer.py search "email login verification code" --strategy structure
```
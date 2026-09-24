$env:PATH = [System.Environment]::GetEnvironmentVariable("PATH", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("PATH", "User")
# Push/Pop instead of Set-Location so Ctrl+C doesn't strand the caller's
# shell in frontend\ (the finally runs even when the pipeline is stopped).
try {
    Push-Location E:\git\EQ2Lexicon\frontend
    npm run dev
} finally {
    Pop-Location
}

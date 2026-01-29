# 🤖 WCAG Audit Agent - Human Thing Style

Narzędzie do automatycznego audytu dostępności cyfrowej (WCAG 2.1), które nie tylko wykrywa błędy, ale **tłumaczy je na ludzki język** i generuje rekomendacje w stylu "Human Thing".

Aplikacja łączy w sobie:
1. **Selenium + Axe-core**: Do technicznego skanowania strony.
2. **Anthropic Claude (AI)**: Do analizy błędów i pisania empatycznych rekomendacji.
3. **Streamlit**: Jako interfejs użytkownika.

## 🚀 Funkcje

- **Automatyczny audyt**: Skanuje podane adresy URL (Home, Category, Product) pod kątem WCAG.
- **Humanizator błędów**: Zamienia techniczne komunikaty (np. *"Ensure buttons have discernible text"*) na proste instrukcje dla ludzi (np. *"Przycisk nie ma etykiety, przez co osoba niewidoma nie wie, do czego służy"*).
- **Priorytetyzacja**: Sortuje błędy według wpływu na użytkownika.
- **Gotowe do Chmury**: Skonfigurowane do działania na Streamlit Cloud (headless Chrome).

## 🛠 Instalacja lokalna

1. **Sklonuj repozytorium:**
   ```bash
   git clone [https://github.com/twoj-user/twoje-repo.git](https://github.com/twoj-user/twoje-repo.git)
   cd twoje-repo

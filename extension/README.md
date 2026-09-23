# Расширение «Marketplace Auto — возвраты WB»

Берёт код получения из профиля покупателя, под которым выполнен вход на wildberries.ru, и
отдаёт его бэкенду Marketplace Auto. Зачем и как устроено — в
[docs/automations/WB_RETURNS.md](../docs/automations/WB_RETURNS.md), раздел «Расширение».

```bash
npm ci
npm run pack   # dist/ и marketplace-auto-returns.zip
```

Установка: `chrome://extensions` → «Режим разработчика» → «Загрузить распакованное расширение» →
папка `dist`. Настройки: адрес сервиса и шестизначный код из бота (`/extension`) или со
страницы «Возвраты WB» → «Расширение».

В образе фронта архив собирается сам и раздаётся по `/extension/marketplace-auto-returns.zip`.

import {
  Calculator,
  ClipboardCheck,
  FileCheck2,
  Bot,
  Gauge,
  Grid3x3,
  MessagesSquare,
  MessageSquareText,
  Package,
  ReceiptText,
  ScanSearch,
  Truck,
  Undo2,
  type LucideIcon,
} from "lucide-react";

export interface AutomationNavItem {
  id: string;
  title: string;
  icon: LucideIcon;
}

export const automationNav: AutomationNavItem[] = [
  { id: "wb-reviews", title: "Мониторинг отзывов", icon: MessageSquareText },
  { id: "wb-turnover", title: "Оборачиваемость", icon: Gauge },
  { id: "wb-card-checklist", title: "Чек-лист карточек", icon: ClipboardCheck },
  { id: "wb-card-documents", title: "Документы карточек WB", icon: FileCheck2 },
  { id: "wb-fbs-stocks", title: "Остатки FBS", icon: Grid3x3 },
  { id: "wb-fbs-penalties", title: "Штрафы FBS", icon: ReceiptText },
  { id: "wb-review-chats", title: "Чаты после отзыва", icon: MessagesSquare },
  { id: "wb-support-bot", title: "Бот поддержки", icon: Bot },
  { id: "wb-returns", title: "Возвраты WB", icon: Undo2 },
  { id: "wb-podsort", title: "Подсорт WB", icon: Truck },
  { id: "fin-reports", title: "Финансовые отчёты", icon: Calculator },
  { id: "wb-box-stickers", title: "Стикеры коробов", icon: Package },
  { id: "wb-screenshot-control", title: "Контроль скриншотов ДБС", icon: ScanSearch },
];

export function automationIcon(id: string): LucideIcon {
  return automationNav.find((item) => item.id === id)?.icon ?? MessageSquareText;
}

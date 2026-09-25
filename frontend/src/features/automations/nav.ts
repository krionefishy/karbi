import {
  ClipboardCheck,
  Gauge,
  Grid3x3,
  MessagesSquare,
  MessageSquareText,
  Package,
  ReceiptText,
  Truck,
  Undo2,
  Warehouse,
  type LucideIcon,
} from "lucide-react";

export interface AutomationNavItem {
  id: string;
  /** Короткое имя для навигации; полное живёт на странице автоматизации. */
  title: string;
  icon: LucideIcon;
}

/** Порядок — как в левой навигации. Каталог на главной приходит с сервера,
 * навигация же должна рисоваться до любого запроса, поэтому список статичен. */
export const automationNav: AutomationNavItem[] = [
  { id: "wb-reviews", title: "Мониторинг отзывов", icon: MessageSquareText },
  { id: "wb-turnover", title: "Оборачиваемость", icon: Gauge },
  { id: "wb-fbs-distribution", title: "Распределение FBS", icon: Warehouse },
  { id: "wb-card-checklist", title: "Чек-лист карточек", icon: ClipboardCheck },
  { id: "wb-fbs-stocks", title: "Остатки FBS", icon: Grid3x3 },
  { id: "wb-fbs-penalties", title: "Штрафы FBS", icon: ReceiptText },
  { id: "wb-review-chats", title: "Чаты после отзыва", icon: MessagesSquare },
  { id: "wb-returns", title: "Возвраты WB", icon: Undo2 },
  { id: "wb-podsort", title: "Подсорт WB", icon: Truck },
  { id: "wb-box-stickers", title: "Стикеры коробов", icon: Package },
];

export function automationIcon(id: string): LucideIcon {
  return automationNav.find((item) => item.id === id)?.icon ?? MessageSquareText;
}

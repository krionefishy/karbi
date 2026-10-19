export interface Employee {
  id: string;
  username: string;
  is_active: boolean;
  is_admin: boolean;
  created_at: string;
  last_login_at: string | null;
}

export interface IssuedPassword {
  user: Employee;
  password: string;
}

export interface EmployeeUpdate {
  is_active?: boolean;
  is_admin?: boolean;
}

export interface BotDelivery {
  last_sent_at: string | null;
  failed_count: number;
  failed_since: string | null;
  last_error: string | null;
}

export interface NotificationBot {
  id: string;
  code: string;
  title: string;
  invite_link_template: string;
  delivery: BotDelivery;
}

export interface BotCreate {
  code: string;
  title: string;
  token: string;
}

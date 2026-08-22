export interface Employee {
  id: string;
  username: string;
  is_active: boolean;
  is_admin: boolean;
  created_at: string;
  last_login_at: string | null;
}

/** The generated password comes back once, on creation and on reset. */
export interface IssuedPassword {
  user: Employee;
  password: string;
}

export interface EmployeeUpdate {
  is_active?: boolean;
  is_admin?: boolean;
}

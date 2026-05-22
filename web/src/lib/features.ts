/**
 * 功能开关:控制哪些模块对用户可见。
 * 后期后台支持动态创建 Agent / 用户管理后,把对应开关打开。
 */
export const FEATURES = {
  // 是否暴露 Agent 管理(单 Agent 后台时关闭)
  AGENTS: false,
  // 是否暴露后台管理(用户/模型/审计)
  ADMIN: false,
  // 是否启用注册功能
  REGISTRATION: false,
} as const;

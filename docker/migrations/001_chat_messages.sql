-- ============================================================
-- 聊天记录持久化 - Supabase 数据库迁移脚本
-- 运行方式：在 Supabase Dashboard -> SQL Editor 中执行
-- ============================================================

-- 1. 为 chat_history 增加标题字段 (用于侧边栏显示)
ALTER TABLE chat_history ADD COLUMN IF NOT EXISTS title TEXT;

-- 2. 创建消息明细表
-- 用于存储具体的对话内容，支持前端实时渲染和搜索
CREATE TABLE IF NOT EXISTS chat_messages (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    chat_id UUID NOT NULL REFERENCES chat_history(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content TEXT NOT NULL,
    metadata JSONB DEFAULT '{}'::jsonb,  -- 存储 RAG sources、token 消耗等
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 3. 为 chat_id 添加索引以优化查询性能
CREATE INDEX IF NOT EXISTS idx_chat_messages_chat_id ON chat_messages(chat_id);

-- 4. 为 created_at 添加索引以支持按时间排序
CREATE INDEX IF NOT EXISTS idx_chat_messages_created_at ON chat_messages(created_at);

-- 5. (可选) 启用 RLS 并设置策略
-- 如果您的 Supabase 项目启用了 RLS，请取消以下注释并根据需要调整

-- ALTER TABLE chat_messages ENABLE ROW LEVEL SECURITY;

-- CREATE POLICY "Users can view their own messages" ON chat_messages
--     FOR SELECT USING (
--         chat_id IN (
--             SELECT id FROM chat_history WHERE user_id = auth.uid()
--         )
--     );

-- CREATE POLICY "Users can insert their own messages" ON chat_messages
--     FOR INSERT WITH CHECK (
--         chat_id IN (
--             SELECT id FROM chat_history WHERE user_id = auth.uid()
--         )
--     );

-- ============================================================
-- 验证：执行以下查询检查表是否创建成功
-- SELECT column_name, data_type FROM information_schema.columns 
-- WHERE table_name = 'chat_messages';
-- ============================================================

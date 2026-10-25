import { sqliteTable, text, integer } from 'drizzle-orm/sqlite-core';
export const saves = sqliteTable('saves', { userId: text('user_id').primaryKey(), revision: integer('revision').notNull(), payload: text('payload').notNull(), updatedAt: text('updated_at').notNull() });
export const archives = sqliteTable('archives', { id: text('id').primaryKey(), userId: text('user_id').notNull(), payload: text('payload').notNull(), createdAt: text('created_at').notNull() });

with Arthexis.ORM;

package body Apps.Migrations.Functions.Migrations_Functions is

   procedure Install (Conn : in out Arthexis.ORM.Database_Connection) is
   begin
      Arthexis.ORM.Execute
        (Conn,
         "CREATE VIEW IF NOT EXISTS migrations_pending AS "
         & "SELECT m.app_name, m.migration_id, m.parent_ids, m.merge_group "
         & "FROM migrations_migration m "
         & "LEFT JOIN migrations_applied a "
         & "ON a.app_name = m.app_name AND a.migration_id = m.migration_id "
         & "WHERE a.id IS NULL "
         & "ORDER BY m.app_name, m.migration_id;");

      Arthexis.ORM.Execute
        (Conn,
         "INSERT OR IGNORE INTO migrations_migration("
         & "app_name, migration_id, parent_ids, merge_group, migration_sql"
         & ") VALUES "
         & "('app', '0001_initial', '', 'main', ''), "
         & "('model', '0001_initial', '', 'main', ''), "
         & "('product', '0001_initial', '', 'main', '');");
   end Install;

end Apps.Migrations.Functions.Migrations_Functions;

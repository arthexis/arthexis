with Arthexis.ORM;

package body Apps.Migrations.Models.Migrations_Models is

   procedure Install (Conn : in out Arthexis.ORM.Database_Connection) is
   begin
      Arthexis.ORM.Execute
        (Conn,
         "CREATE TABLE IF NOT EXISTS migrations_migration ("
         & "id INTEGER PRIMARY KEY, "
         & "app_name TEXT NOT NULL, "
         & "migration_id TEXT NOT NULL, "
         & "parent_ids TEXT NOT NULL DEFAULT '', "
         & "merge_group TEXT NOT NULL DEFAULT '', "
         & "migration_sql TEXT NOT NULL DEFAULT '', "
         & "UNIQUE(app_name, migration_id), "
         & "FOREIGN KEY (app_name) REFERENCES app_app(app_name)"
         & ");");

      Arthexis.ORM.Execute
        (Conn,
         "CREATE TABLE IF NOT EXISTS migrations_applied ("
         & "id INTEGER PRIMARY KEY, "
         & "app_name TEXT NOT NULL, "
         & "migration_id TEXT NOT NULL, "
         & "applied_at_utc TEXT NOT NULL, "
         & "UNIQUE(app_name, migration_id), "
         & "FOREIGN KEY (app_name, migration_id) REFERENCES migrations_migration(app_name, migration_id)"
         & ");");
   end Install;

end Apps.Migrations.Models.Migrations_Models;

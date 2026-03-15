package body Apps.Migrations.Views.Migrations_Views is

   function Pending_Migrations_SQL return String is
   begin
      return
        "SELECT app_name, migration_id, parent_ids, merge_group "
        & "FROM migrations_pending "
        & "ORDER BY app_name, migration_id";
   end Pending_Migrations_SQL;

end Apps.Migrations.Views.Migrations_Views;

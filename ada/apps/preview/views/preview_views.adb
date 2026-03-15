package body Apps.Preview.Views.Preview_Views is

   function Enabled_Snapshots_SQL return String is
   begin
      return
        "SELECT product_name, preview_name, path, template_name "
        & "FROM preview_enabled_snapshots "
        & "ORDER BY product_name, preview_name";
   end Enabled_Snapshots_SQL;

end Apps.Preview.Views.Preview_Views;

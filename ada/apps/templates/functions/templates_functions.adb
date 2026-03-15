with Arthexis.ORM;

package body Apps.Templates.Functions.Templates_Functions is

   procedure Install (Conn : in out Arthexis.ORM.Database_Connection) is
   begin
      Arthexis.ORM.Execute
        (Conn,
         "CREATE VIEW IF NOT EXISTS templates_index AS "
         & "SELECT app_name, template_name "
         & "FROM templates_template "
         & "ORDER BY app_name, template_name;");
   end Install;

end Apps.Templates.Functions.Templates_Functions;

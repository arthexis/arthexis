with Arthexis.ORM;

package body Apps.Templates.Models.Templates_Models is

   procedure Install (Conn : in out Arthexis.ORM.Database_Connection) is
   begin
      Arthexis.ORM.Execute
        (Conn,
         "CREATE TABLE IF NOT EXISTS templates_template ("
         & "id INTEGER PRIMARY KEY, "
         & "app_name TEXT NOT NULL, "
         & "template_name TEXT NOT NULL, "
         & "UNIQUE(app_name, template_name)"
         & ");");
   end Install;

end Apps.Templates.Models.Templates_Models;
